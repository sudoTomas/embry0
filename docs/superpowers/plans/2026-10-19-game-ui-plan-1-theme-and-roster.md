# Game UI, plan 1 of 3: theme and roster (issues H to K) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One theme token set with the pixel display face, a roster of twelve CSS-animated SVG pixel sprites tinted by the agent's palette colour, sprites on every surface that shows an agent, and one `PartyNode` renderer shared by the pipeline editor and the live graph.

**Architecture:** `index.css` keeps the only `@theme`; the second token set, the dead keyframes and the hard-coded hex go. `components/roster/` holds the sprite grids as strings compiled once into SVG paths, a `Sprite` component that stacks the state's frames and steps them with one CSS keyframe, and `stateFor` helpers that turn job and node state into a sprite state. `AgentAvatar` (the workstream 2 seam) renders a `Sprite`, so every existing caller gets a sprite; the console cards, the agent form and the two graphs get state-aware sprites on purpose. `PartyNode` replaces the editor's `AgentNode` and the graph view's `LiveAgentNode`; `circularLayout` detects sentinels by `data.nodeRole`, the shape `deserializeGraph` actually produces.

**Tech Stack:** React 19, Tailwind v4 (`@theme` tokens in `index.css`), `@xyflow/react` 12, vitest 5 + Testing Library, TypeScript 5.9. No new runtime dependency; one Google Fonts face (Pixelify Sans) through the existing `@import`.

**Spec:** `docs/superpowers/specs/2026-10-18-game-ui-design.md` (sections 3.1 to 3.4, 3.10, 4, 5 and issues H to K of section 6). Workstream 2's identity module is `frontend/src/lib/agentIdentity.ts`; its contract is `docs/contracts/identity.json`.

## Global Constraints

- Dark only. No light theme, no theme switch.
- No new runtime dependency: no motion library, no canvas, no image files. The one addition is the `Pixelify Sans` face from Google Fonts, loaded through the existing `@import` in `index.css`; the public dashboard CSP already allows `https://fonts.googleapis.com` for styles and `https://fonts.gstatic.com` for fonts (`frontend/nginx/dashboard-public.conf.template`).
- The pixel face is used by exactly three CSS classes: `.hud`, `.title-pixel` and `.sprite-nameplate`. No component uses the `font-pixel` utility directly; a test scans the sources.
- `frontend/src/lib/agentIdentity.ts` is the only identity source. No surface keeps its own colour, icon or role map. `resolveAgentType` maps the template spelling `reviewer` to the `review` row.
- Sprites: twelve avatars, one per `AVATARS` id, each a `Record<SpriteState, Frame[]>` where a `Frame` is sixteen strings of sixteen characters over `.` `o` `f` `h` `e`. States and frame counts: `idle` 2, `thinking` 2, `tool` 2, `done` 1, `failed` 1, `pending` 1. Total source of `sprites.ts` under 40 KB. Two-frame states step at 1.2 s per cycle, never faster than two frames per second, gated by `prefers-reduced-motion: no-preference`; reduced motion shows the first frame static. One sprite per card or node, never a second animation on it.
- `data-divine="off"` keeps its meaning (the divine layer only); sprites are not divine elements and keep rendering with it off. Rule 9 in `frontend/src/components/divine/CLAUDE.md` says so.
- The start and end sentinels keep the gold sphere (`StartEndNode`). Sidebar, TopBar and navigation icons stay lucide.
- No backend change in this plan. Issues L to Q are the next two plans.
- Vendor-neutral naming: no customer, deployment, host or person name anywhere in the tree (`CONTRIBUTING.md`).
- Commits follow `.claude/skills/commit-standard` and end with `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.
- Frontend gate for every task: `npm run build` (tsc -b + vite build), `npm run lint` (0 errors), `npm test` (vitest), all run from `frontend/`.

## Review Focus

Five inputs the spec implies but names no test for; each line's test is in the task that owns the code.

1. **An avatar or colour id the frontend does not know** (a newer backend adds a thirteenth sprite): `Sprite` renders `golem` and `slate`, never a blank. Pinned in Task 2 (`Sprite.test.tsx`, "unknown ids fall back").
2. **Reduced motion**: a user with `prefers-reduced-motion: reduce` sees the first frame of every sprite, static, and no ring flash. jsdom does not evaluate media queries, so the test reads `roster.css` and `party.css` and asserts every `animation:` sits inside a `prefers-reduced-motion: no-preference` block, and that frame 1 is hidden outside it. Pinned in Task 2 and Task 4.
3. **A finished job with no recorded stage** (`current_stage` null on a legacy row): `DoneFailedCard` renders without a sprite and without an error. Pinned in Task 3.
4. **A queued job whose template the dashboard cannot resolve** (`pipeline_template` null, or a name no template summary carries): `QueuedCard` shows the triage sprite in `pending`, because every default route starts at triage. Pinned in Task 3.
5. **A live graph node whose agent row was deleted** (an `agent_type` with no row and no builtin): `PartyNode` shows the slug on the nameplate and the `golem` sprite in `slate`. Pinned in Task 4.

---

## Wave plan and file ownership

Two waves of two parallel implementers. A file is owned by exactly one task; a task that needs a change in another task's file asks the controller instead of editing it.

| Wave | Task | Issue | Owns (create or modify) |
|---|---|---|---|
| 1 | 1 | H | `frontend/src/index.css`, `frontend/index.html`, `frontend/src/lib/constants.ts`, `frontend/src/lib/utils.ts`, `frontend/src/components/logs/AgentFilterTabs.tsx`, `frontend/src/components/auth/AuthGate.tsx`, `frontend/src/components/dashboard/LiveActivityBand.tsx`, `frontend/src/pages/settings/ApiKeysTab.tsx`, `frontend/src/pages/admin/UsersAdminPage.tsx`, `frontend/src/components/issues/AgentIndicator.tsx`, `frontend/src/components/layout/Sidebar.tsx`, `frontend/src/components/layout/TopBar.tsx`, `frontend/src/components/pipeline-editor/PipelineEditor.tsx` (hex only), `frontend/src/components/pipeline-editor/StartEndNode.tsx`, `frontend/src/components/pipeline-editor/AgentBar.tsx` (hex only), `frontend/src/components/pipeline-editor/FeedbackEdge.tsx`, `frontend/src/components/pipeline-editor/hooks/useGraphState.ts`, `frontend/src/components/pipeline/PipelineGraphView.tsx:35` (one class), every `<h1>` under `frontend/src/pages/**`, `frontend/src/__tests__/theme.test.ts`, `frontend/src/__tests__/userFacingText.test.ts`, `frontend/src/components/logs/__tests__/AgentFilterTabs.test.tsx` |
| 1 | 2 | I | `frontend/src/components/roster/sprites.ts`, `frontend/src/components/roster/Sprite.tsx`, `frontend/src/components/roster/roster.css`, `frontend/src/components/roster/spriteState.ts`, `frontend/src/components/roster/index.ts`, `frontend/src/components/roster/__tests__/*`, `frontend/src/components/divine/CLAUDE.md`, `docs/glossary.md` (Roster and Sprite entries only) |
| 2 | 3 | J | `frontend/src/components/agents/AgentAvatar.tsx` (+test), `frontend/src/components/agents/AgentIdentityFields.tsx` (+test), `frontend/src/pages/AgentsPage.tsx`, `frontend/src/hooks/useLiveJobSummary.ts` (+test), `frontend/src/components/console/RunningCard.tsx`, `QueuedCard.tsx`, `DoneFailedCard.tsx`, `frontend/src/components/console/firstPartyMember.ts`, `frontend/src/components/console/__tests__/*` |
| 2 | 4 | K | `frontend/src/components/pipeline/PartyNode.tsx`, `frontend/src/components/pipeline/party.css`, `frontend/src/components/pipeline/PipelineGraphView.tsx`, `frontend/src/components/pipeline/__tests__/*`, `frontend/src/components/pipeline-editor/PipelineEditor.tsx` (node registration only), `frontend/src/components/pipeline-editor/AgentNode.tsx` (delete), `frontend/src/components/pipeline-editor/circularLayout.ts` (+test), `docs/glossary.md` (PartyNode entry, AgentBar wording) |

Task 2 does not touch `index.css`: the roster's CSS lives in `roster.css`, imported by `Sprite.tsx`. Task 4's `party.css` likewise. Wave 2 starts after wave 1 is integrated on `feat/game-ui`, so Tasks 3 and 4 import `@/components/roster` and the Task 1 tokens as they exist on the branch.

---

### Task 1: Theme consolidation (issue H)

**Files:**
- Modify: `frontend/src/index.css`
- Modify: `frontend/index.html`
- Modify: `frontend/src/lib/constants.ts`
- Modify: `frontend/src/lib/utils.ts:81`
- Modify: `frontend/src/components/logs/AgentFilterTabs.tsx`
- Modify: `frontend/src/components/auth/AuthGate.tsx:153,211`
- Modify: `frontend/src/components/dashboard/LiveActivityBand.tsx:27`
- Modify: `frontend/src/pages/settings/ApiKeysTab.tsx:251`
- Modify: `frontend/src/pages/admin/UsersAdminPage.tsx:75`
- Modify: `frontend/src/components/issues/AgentIndicator.tsx:17`
- Modify: `frontend/src/components/layout/Sidebar.tsx:223`
- Modify: `frontend/src/components/layout/TopBar.tsx:68`
- Modify: `frontend/src/components/pipeline-editor/PipelineEditor.tsx:86,88-89,187,233,289`
- Modify: `frontend/src/components/pipeline-editor/StartEndNode.tsx:37-38,76-77`
- Modify: `frontend/src/components/pipeline-editor/AgentBar.tsx:67`
- Modify: `frontend/src/components/pipeline-editor/FeedbackEdge.tsx:13,26`
- Modify: `frontend/src/components/pipeline-editor/hooks/useGraphState.ts:48`
- Modify: `frontend/src/components/pipeline/PipelineGraphView.tsx:35` (drop one class; Task 4 rewrites the file in wave 2)
- Modify: every `<h1>` in `frontend/src/pages/**/*.tsx` (30 files; `grep -rln '<h1' frontend/src/pages`)
- Test: `frontend/src/__tests__/theme.test.ts` (create)
- Test: `frontend/src/__tests__/userFacingText.test.ts` (extend)
- Test: `frontend/src/components/logs/__tests__/AgentFilterTabs.test.tsx` (create)

**Interfaces:**
- Consumes: `PALETTE`, `PALETTE_HEX` from `@/lib/agentIdentity`; `useIdentityLookup` from `@/hooks/useAgentIdentity`.
- Produces: theme tokens `--font-pixel`, `--color-palette-{gold,cyan,violet,rose,emerald,amber,sky,slate}`, `--color-canvas`, `--color-elevated`, `--color-grid`, `--color-feedback`; classes `.hud`, `.title-pixel`; the Tailwind utilities they imply (`bg-canvas`, `text-palette-cyan`, `border-feedback`, …). Tasks 2 and 4 read `var(--color-palette-<id>)`, `var(--color-background)`, `var(--color-foreground)` and `var(--font-pixel)`; nothing else.

Do not touch `AgentNode.tsx` (Task 4 deletes it), `PipelineGraphView.tsx` beyond line 35 (Task 4 rewrites it), `AgentDetailPopup.tsx`, `EdgeDetailPopup.tsx`, `TemplateDrawer.tsx` or `TemplatePicker.tsx` (plan 3, issue O, moves them onto `Dialog` and removes `popup-in` and `animate-in` with them).

- [ ] **Step 1: Write the failing theme test**

`frontend/src/__tests__/theme.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import css from "../index.css?raw";
import html from "../../index.html?raw";
import { PALETTE, PALETTE_HEX } from "@/lib/agentIdentity";

/**
 * One theme (spec 3.1). index.css holds the only token set; the palette
 * tokens equal the identity module's hex so a sprite tinted by CSS and a
 * badge tinted by class agree; the second token set, the role and stage
 * tokens and the dead keyframes are gone; the chrome that hard-coded hex
 * reads tokens. The pixel face is a token three classes use.
 */

const CHROME = import.meta.glob(
  [
    "/src/components/layout/Sidebar.tsx",
    "/src/components/layout/TopBar.tsx",
    "/src/components/pipeline-editor/PipelineEditor.tsx",
    "/src/components/pipeline-editor/StartEndNode.tsx",
    "/src/components/pipeline-editor/AgentBar.tsx",
    "/src/components/pipeline-editor/FeedbackEdge.tsx",
    "/src/components/pipeline-editor/hooks/useGraphState.ts",
  ],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const SOURCES = import.meta.glob(
  ["/src/**/*.{ts,tsx,css}", "!/src/**/__tests__/**", "!/src/**/*.test.{ts,tsx}", "!/src/index.css"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

function theme(): string {
  const start = css.indexOf("@theme {");
  const end = css.indexOf("\n}", start);
  expect(start, "index.css has one @theme block").toBeGreaterThan(-1);
  return css.slice(start, end);
}

describe("theme tokens", () => {
  it("imports Outfit, JetBrains Mono and Pixelify Sans, and declares the pixel face", () => {
    expect(css.split("\n")[0]).toMatch(/^@import url\('https:\/\/fonts\.googleapis\.com\/css2\?family=Outfit.*JetBrains\+Mono.*Pixelify\+Sans.*display=swap'\);$/);
    expect(theme()).toContain("--font-pixel: 'Pixelify Sans', monospace;");
    expect(html).not.toContain("IBM Plex");
    expect(html).not.toMatch(/fonts\.googleapis\.com\/css2/);
    expect(html).toContain('rel="preconnect" href="https://fonts.googleapis.com"');
  });

  it("declares one palette token per identity colour, equal to PALETTE_HEX", () => {
    const block = theme();
    for (const id of PALETTE) {
      expect(block).toContain(`--color-palette-${id}: ${PALETTE_HEX[id]};`);
    }
  });

  it("declares the surface tokens the chrome reads", () => {
    const block = theme();
    for (const token of ["--color-canvas:", "--color-elevated:", "--color-grid:", "--color-feedback:"]) {
      expect(block).toContain(token);
    }
  });

  it("has no second token set, no role or stage tokens and no dead keyframes", () => {
    expect(css).not.toMatch(/--embry0-/);
    expect(css).not.toContain(".embry0-card");
    expect(css).not.toContain(".embry0-spinner");
    expect(css).not.toMatch(/--color-(role|stage)-/);
    for (const name of ["embry0-flow-down", "embry0-pulse-border", "embry0-spin", "agent-bounce", "pulse-glow"]) {
      expect(css, `keyframes ${name} removed`).not.toContain(`@keyframes ${name}`);
    }
    expect(css).not.toMatch(/\.glow-(cyan|amber|green|purple|blue)/);
  });

  it("writes the React Flow overrides with tokens, not hex", () => {
    const start = css.indexOf("/* React Flow");
    const end = css.indexOf("/*", start + 2);
    const block = css.slice(start, end);
    expect(block.length).toBeGreaterThan(100);
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });

  it("uses the pixel face in exactly .hud and .title-pixel here", () => {
    const rules = [...css.matchAll(/\n(\.[a-z-]+)\s*\{[^}]*var\(--font-pixel\)[^}]*\}/g)].map((m) => m[1]);
    expect(rules.sort()).toEqual([".hud", ".title-pixel"]);
  });
});

describe("chrome", () => {
  it("hard-codes no hex colour", () => {
    for (const [file, source] of Object.entries(CHROME)) {
      const hits = [...source.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0]);
      expect(hits, file).toEqual([]);
    }
  });

  it("no source uses the legacy card, the role tokens or the pixel utility directly", () => {
    const hits: string[] = [];
    for (const [file, source] of Object.entries(SOURCES)) {
      if (/\bembry0-card\b/.test(source)) hits.push(`${file}: embry0-card`);
      if (/\b(text|bg|border)-(role|stage)-/.test(source)) hits.push(`${file}: role/stage token`);
      if (/\bfont-pixel\b/.test(source) && !file.endsWith(".css")) hits.push(`${file}: font-pixel`);
      if (/animate-agent-bounce|animate-pulse-glow/.test(source)) hits.push(`${file}: dead animation class`);
    }
    expect(hits).toEqual([]);
  });

  it("gives every page heading the pixel face", () => {
    const pages = Object.entries(SOURCES).filter(([file]) => file.startsWith("/src/pages/") && file.endsWith(".tsx"));
    expect(pages.length).toBeGreaterThan(20);
    const bare: string[] = [];
    for (const [file, source] of pages) {
      for (const m of source.matchAll(/<h1\b[^>]*>/g)) {
        if (!/\btitle-pixel\b/.test(m[0])) bare.push(`${file}: ${m[0]}`);
      }
    }
    expect(bare).toEqual([]);
  });
});
```

`frontend/src/__tests__/userFacingText.test.ts`: add after the existing `describe`:

```ts
describe("pixel face (spec 3.1)", () => {
  it("is reachable only through .hud, .title-pixel and .sprite-nameplate", () => {
    const styles = import.meta.glob(["/src/**/*.css"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const owners = new Set<string>();
    for (const [file, source] of Object.entries(styles)) {
      for (const m of source.matchAll(/\n(\.[a-z-]+)\s*\{[^}]*var\(--font-pixel\)[^}]*\}/g)) owners.add(m[1]);
      const loose = source.replace(/\n\.[a-z-]+\s*\{[^}]*\}/g, "");
      expect(loose.includes("var(--font-pixel)"), `${file}: --font-pixel outside a class rule`).toBe(false);
    }
    const allowed = [".hud", ".title-pixel", ".sprite-nameplate"];
    expect([...owners].filter((o) => !allowed.includes(o))).toEqual([]);
    expect(owners.has(".hud") && owners.has(".title-pixel")).toBe(true);
  });
});
```

(`.sprite-nameplate` arrives with Task 2's `roster.css`; in wave 1 the set is `.hud` and `.title-pixel`, which the assertion allows.)

`frontend/src/components/logs/__tests__/AgentFilterTabs.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber" },
      { type: "review", display_name: "Reviewer", avatar: "warden", color: "rose" },
    ],
  }),
}));
import { AgentFilterTabs } from "../AgentFilterTabs";

const states = {
  developer: { node_id: "developer", agent_type: "developer", state: "completed" as const, iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 },
  reviewer: { node_id: "reviewer", agent_type: "reviewer", state: "running" as const, iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 },
};

describe("AgentFilterTabs", () => {
  it("colours each tab from the agent's identity, resolving the reviewer spelling", () => {
    render(<AgentFilterTabs nodeStates={states} selectedNodeId={null} onSelect={() => {}} />);
    expect(screen.getByText("Developer").className).toContain("text-amber-400");
    expect(screen.getByText("Reviewer").className).toContain("text-rose-400");
  });

  it("labels a tab with the display name, not a re-cased slug", () => {
    render(<AgentFilterTabs nodeStates={states} selectedNodeId={null} onSelect={() => {}} />);
    expect(screen.queryByText("Developer")).toBeInTheDocument();
    expect(screen.queryByText("Reviewer")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `frontend/`: `npx vitest run src/__tests__/theme.test.ts src/__tests__/userFacingText.test.ts src/components/logs/__tests__/AgentFilterTabs.test.tsx`
Expected: FAIL. theme: `--font-pixel` missing, `--embry0-` present, hex in chrome, bare `<h1>`s. userFacingText: `.hud` missing. AgentFilterTabs: `text-amber-400` absent (the tab uses `ROLE_COLORS`).

- [ ] **Step 3: Rewrite the theme in `index.css`**

Line 1 becomes:

```css
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Pixelify+Sans:wght@400;600&display=swap');
```

In `@theme`, replace lines 27 to 38 (the role and stage colours) with:

```css
  /* Identity palette: one token per PALETTE id in lib/agentIdentity.ts
     (PALETTE_HEX mirrors these; theme.test.ts pins them equal). gold is
     --color-primary; the rest are the Tailwind 400 shades. */
  --color-palette-gold: #d4af37;
  --color-palette-cyan: #22d3ee;
  --color-palette-violet: #a78bfa;
  --color-palette-rose: #fb7185;
  --color-palette-emerald: #34d399;
  --color-palette-amber: #fbbf24;
  --color-palette-sky: #38bdf8;
  --color-palette-slate: #94a3b8;

  /* Surfaces the chrome used to hard-code */
  --color-canvas: #0c1015;    /* sidebar, editor and graph canvases */
  --color-elevated: #1a1d2e;  /* lifted surfaces: control hover, popovers */
  --color-grid: #1e2040;      /* React Flow background dots */
  --color-feedback: #f87171;  /* feedback (loop) edges */
```

After `--font-display` add:

```css
  --font-pixel: 'Pixelify Sans', monospace;
```

After the `h1, h2, …` rule add:

```css
/* The pixel face (spec 3.1): HUD numerals and labels, page titles and the
   sprite nameplate (components/roster/roster.css). Nothing else. */
.hud {
  font-family: var(--font-pixel);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}

.title-pixel {
  font-family: var(--font-pixel);
  letter-spacing: 0.01em;
}
```

Delete lines 143 to 157 (`pulse-glow`, `.animate-pulse-glow`, the five `.glow-*` classes). `animate-pulse-glow` is used by `LiveActivityBand.tsx:30` and `PipelineGraphView.tsx:35`; both lose the class in this task (the band's icon stays; the graph view's line becomes `const runningClass = "";`, and Task 4 deletes the whole renderer in wave 2).

Replace the React Flow block (lines 159 to 182) with:

```css
/* React Flow dark theme overrides */
.react-flow__minimap {
  background-color: var(--color-card) !important;
  border: 1px solid color-mix(in srgb, var(--color-foreground) 6%, transparent) !important;
  border-radius: 8px !important;
}

.react-flow__controls {
  background-color: transparent !important;
  border: none !important;
  box-shadow: none !important;
}

.react-flow__controls-button {
  background-color: var(--color-card) !important;
  border: 1px solid color-mix(in srgb, var(--color-foreground) 8%, transparent) !important;
  fill: color-mix(in srgb, var(--color-foreground) 50%, transparent) !important;
  border-radius: 6px !important;
}

.react-flow__controls-button:hover {
  background-color: var(--color-elevated) !important;
  fill: color-mix(in srgb, var(--color-foreground) 80%, transparent) !important;
}
```

Delete lines 215 to 270: the `/* ===== embry0 Execution Dashboard Theme ===== */` `:root` block, `.embry0-card`, `embry0-flow-down`, `embry0-pulse-border`, `embry0-spin`, `.embry0-spinner`, `agent-bounce` and `.animate-agent-bounce`. Keep `divine-radiate` and everything after it.

- [ ] **Step 4: Drop the stale font load from `index.html`**

Delete lines 10 to 13 (the IBM Plex `<link rel="stylesheet">`). Keep both `preconnect` lines.

- [ ] **Step 5: Trim `lib/constants.ts`**

Delete `ROLE_COLORS` (lines 83 to 89) and everything from `// ===== Pipeline Phase Metadata =====` to the end (`PhaseConfig`, `PIPELINE_PHASES`, `AGENT_STATUS_COLORS`, `getPhaseForAgent`). Nothing else imports them except `AgentFilterTabs` (next step).

- [ ] **Step 6: Move the remaining uses onto tokens and identity**

`lib/utils.ts:81`: `className: "text-role-explorer"` becomes `className: "text-palette-cyan"`.

`components/logs/AgentFilterTabs.tsx`: replace the `ROLE_COLORS` import with `import { useIdentityLookup } from "@/hooks/useAgentIdentity";`, call `const lookup = useIdentityLookup();` at the top of the component, and replace lines 37 to 44 and line 67 with:

```tsx
        const identity = lookup(state.agent_type || nodeId);
        const icon = STATE_ICONS[state.state] || "○";
        const label = identity.displayName;
        …
            <span className={selectedNodeId === nodeId ? undefined : identity.classes.text}>{label}</span>
```

`components/auth/AuthGate.tsx:153,211`, `pages/settings/ApiKeysTab.tsx:251`, `pages/admin/UsersAdminPage.tsx:75`: `embry0-card` becomes `rounded-lg border border-border bg-card` (the `VitalsTile` idiom; the 16 px radius and the cyan glow go).

`components/dashboard/LiveActivityBand.tsx:27-30`: `className="embry0-card animate-fade-up flex …"` becomes `className="rounded-lg border bg-card animate-fade-up flex …"`; keep the inline `borderColor`; drop `animate-pulse-glow` from the `Activity` icon.

`components/issues/AgentIndicator.tsx:17`: drop `animate-agent-bounce` from the class list.

`components/layout/Sidebar.tsx:223`: `bg-[#0c1015]` becomes `bg-canvas`.

`components/layout/TopBar.tsx:68`: delete the inline `style` and add `bg-linear-to-b from-canvas/95 to-background/95` (Tailwind v4's gradient utility) to the element's `className`.

`components/pipeline-editor/PipelineEditor.tsx`: line 86 `className="bg-[#0c0e12]"` becomes `className="bg-canvas"`; lines 88 and 89 `!bg-[rgba(15,20,25,0.9)]` become `!bg-card/90` and `nodeColor="#ffffff20"` becomes `nodeColor="color-mix(in srgb, var(--color-foreground) 12%, transparent)"`; lines 187 and 233 `bg-[#0f1117]` become `bg-card`; line 289 both `bg-[#09090b]` become `bg-background`.

`components/pipeline-editor/StartEndNode.tsx:37-38,76-77`: `!bg-[#0f1419]` becomes `!bg-card`; `border: "2px solid #d4af37"` becomes `border: "2px solid var(--color-primary)"`.

`components/pipeline-editor/AgentBar.tsx:67`: `bg-[#0f1419]` becomes `bg-card`.

`components/pipeline-editor/FeedbackEdge.tsx:13,26` and `hooks/useGraphState.ts:48`: `"#f87171"` becomes `"var(--color-feedback)"`.

- [ ] **Step 7: Give every page heading the pixel face**

In each file `grep -rln '<h1' frontend/src/pages` lists, add `title-pixel` as the first class of every `<h1 className="…">`. Example: `<h1 className="text-2xl font-bold">Agents</h1>` becomes `<h1 className="title-pixel text-2xl font-bold">Agents</h1>`. Headings in `components/` (`AuthGate`, `ErrorBoundary`, the QA views) are not page titles and stay as they are.

- [ ] **Step 8: Run the three test files and the gate**

Run: `npx vitest run src/__tests__/theme.test.ts src/__tests__/userFacingText.test.ts src/components/logs/__tests__/AgentFilterTabs.test.tsx`
Expected: PASS.
Run: `npm run build && npm run lint && npm test`
Expected: tsc clean (any leftover import of a deleted constant fails here), lint 0 errors, every existing test green. `src/pages/__tests__/*` tests that match an `h1` by text still pass; one that matches a class string must be updated in the same commit.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/index.css frontend/index.html frontend/src/lib/constants.ts frontend/src/lib/utils.ts frontend/src/components frontend/src/pages frontend/src/__tests__
git commit -F - <<'EOF'
feat(frontend): one theme with the pixel face and palette tokens

The dashboard carried two token sets (the @theme and an --embry0-*
:root block), role and stage colours no identity read any more, dead
keyframes, a font load nothing referenced and hex colours hard-coded in
the sidebar, the top bar, the editor and the graph. The game UI needs
one place to change a colour.

index.css keeps the only @theme: eight palette tokens equal to
PALETTE_HEX, four surface tokens the chrome used to spell as hex, and
--font-pixel, reachable through .hud and .title-pixel only. The second
token set, .embry0-card, the role and stage tokens, the glow classes
and the dead keyframes are gone; their uses read tokens and identity
classes. Every page title wears .title-pixel.

Spec: docs/superpowers/specs/2026-10-18-game-ui-design.md, 3.1 (H).
The editor node and the live node lose their hex with the renderer
(plan 1, Task 4); the popups and drawers move onto Dialog in plan 3.

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 2: The roster (issue I)

**Files:**
- Create: `frontend/src/components/roster/sprites.ts`
- Create: `frontend/src/components/roster/Sprite.tsx`
- Create: `frontend/src/components/roster/roster.css`
- Create: `frontend/src/components/roster/spriteState.ts`
- Create: `frontend/src/components/roster/index.ts`
- Modify: `frontend/src/components/divine/CLAUDE.md` (rule 9)
- Modify: `docs/glossary.md` (Roster and Sprite entries after `### Browser Backend`)
- Test: `frontend/src/components/roster/__tests__/sprites.test.ts`
- Test: `frontend/src/components/roster/__tests__/Sprite.test.tsx`
- Test: `frontend/src/components/roster/__tests__/spriteState.test.ts`

**Interfaces:**
- Consumes: `AVATARS`, `PALETTE`, `DEFAULT_AVATAR`, `DEFAULT_COLOR`, `isAvatarId`, `isPaletteId`, `AvatarId`, `PaletteId` from `@/lib/agentIdentity`; `AgentState` from `@/hooks/useAgentStates`; `NodeLiveState` from `@/lib/pipeline-live`.
- Produces (from `@/components/roster`):
  - `type SpriteState = "idle" | "thinking" | "tool" | "done" | "failed" | "pending"`
  - `type Frame = readonly string[]` (sixteen rows of sixteen characters)
  - `type CharClass = "o" | "f" | "h" | "e"`
  - `const SPRITE_STATES: readonly SpriteState[]`
  - `const FRAME_COUNTS: Record<SpriteState, 1 | 2>`
  - `const SPRITES: Record<AvatarId, Record<SpriteState, Frame[]>>`
  - `const COMPILED: Record<AvatarId, Record<SpriteState, CompiledFrame[]>>` where `type CompiledFrame = Partial<Record<CharClass, string>>` (path `d` per class, absent when the class has no cell)
  - `function compileFrame(frame: Frame): CompiledFrame`
  - `function Sprite(props: { avatar: string; color: string; state: SpriteState; size?: 16 | 32 | 48 | 64; label?: string; className?: string }): JSX.Element`
  - `function stateFor(agent: Pick<AgentState, "status" | "events">): SpriteState`
  - `function stateForNode(state: NodeLiveState["state"] | undefined): SpriteState`

- [ ] **Step 1: Write the failing sprite data tests**

`frontend/src/components/roster/__tests__/sprites.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import source from "../sprites.ts?raw";
import { AVATARS } from "@/lib/agentIdentity";
import { COMPILED, FRAME_COUNTS, SPRITES, SPRITE_STATES, compileFrame } from "../sprites";

const CELL = /^[.ofhe]{16}$/;

describe("sprites", () => {
  it("has every avatar in every state with the specified frame count", () => {
    expect(SPRITE_STATES).toEqual(["idle", "thinking", "tool", "done", "failed", "pending"]);
    expect(FRAME_COUNTS).toEqual({ idle: 2, thinking: 2, tool: 2, done: 1, failed: 1, pending: 1 });
    for (const avatar of AVATARS) {
      for (const state of SPRITE_STATES) {
        expect(SPRITES[avatar][state], `${avatar}.${state}`).toHaveLength(FRAME_COUNTS[state]);
      }
    }
  });

  it("draws every frame as sixteen rows of sixteen cells over . o f h e", () => {
    for (const avatar of AVATARS) {
      for (const state of SPRITE_STATES) {
        SPRITES[avatar][state].forEach((frame, i) => {
          expect(frame, `${avatar}.${state}[${i}]`).toHaveLength(16);
          for (const row of frame) expect(row, `${avatar}.${state}[${i}] ${JSON.stringify(row)}`).toMatch(CELL);
        });
      }
    }
  });

  it("gives every non-pending frame an outline, a fill and (in idle) eyes", () => {
    for (const avatar of AVATARS) {
      for (const state of SPRITE_STATES) {
        COMPILED[avatar][state].forEach((paths, i) => {
          expect(paths.o, `${avatar}.${state}[${i}] outline`).toMatch(/^M/);
          if (state !== "pending") expect(paths.f, `${avatar}.${state}[${i}] fill`).toMatch(/^M/);
        });
      }
      expect(COMPILED[avatar].idle[0].e, `${avatar} idle eyes`).toMatch(/^M/);
    }
  });

  it("alternates the two frames of a two-frame state", () => {
    for (const avatar of AVATARS) {
      for (const state of ["idle", "thinking", "tool"] as const) {
        const [a, b] = SPRITES[avatar][state];
        expect(a.join("\n"), `${avatar}.${state} frames differ`).not.toEqual(b.join("\n"));
      }
    }
  });

  it("compiles runs into one rectangle path per class", () => {
    const frame = ["o.ff............", ...Array.from({ length: 15 }, () => "................")];
    expect(compileFrame(frame)).toEqual({ o: "M0 0h1v1h-1z", f: "M2 0h2v1h-2z" });
  });

  it("stays under the 40 KB source budget (divine CLAUDE.md rule 9)", () => {
    expect(source.length).toBeLessThan(40_000);
  });
});
```

- [ ] **Step 2: Write the failing Sprite component test**

`frontend/src/components/roster/__tests__/Sprite.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import css from "../roster.css?raw";
import { Sprite } from "../Sprite";

describe("Sprite", () => {
  it("renders a crisp 16x16 image tinted by the palette token, named for assistive tech", () => {
    const { container } = render(<Sprite avatar="smith" color="amber" state="idle" size={32} label="Developer, idle" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("role", "img");
    expect(svg).toHaveAttribute("aria-label", "Developer, idle");
    expect(svg).toHaveAttribute("viewBox", "0 0 16 16");
    expect(svg).toHaveAttribute("width", "32");
    expect(svg).toHaveAttribute("shape-rendering", "crispEdges");
    expect(svg).toHaveAttribute("data-avatar", "smith");
    expect(svg).toHaveAttribute("data-color", "amber");
    expect(svg).toHaveAttribute("data-state", "idle");
    expect(svg.style.getPropertyValue("--sprite-fill")).toBe("var(--color-palette-amber)");
    const fills = [...svg.querySelectorAll("path")].map((p) => p.getAttribute("fill"));
    expect(fills).toContain("var(--color-background)");
    expect(fills).toContain("var(--sprite-fill)");
    expect(fills).toContain("var(--color-foreground)");
  });

  it("stacks both frames of a two-frame state and one frame otherwise", () => {
    const { container, rerender } = render(<Sprite avatar="seer" color="violet" state="thinking" />);
    expect(container.querySelector("svg")).toHaveAttribute("data-frames", "2");
    expect(container.querySelectorAll("g.sprite-frame")).toHaveLength(2);
    expect(container.querySelector('g[data-frame="1"]')).not.toBeNull();
    rerender(<Sprite avatar="seer" color="violet" state="done" />);
    expect(container.querySelector("svg")).toHaveAttribute("data-frames", "1");
    expect(container.querySelectorAll("g.sprite-frame")).toHaveLength(1);
  });

  it("falls back to golem and slate for ids it does not know", () => {
    const { container } = render(<Sprite avatar="dragon" color="mauve" state="idle" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("data-avatar", "golem");
    expect(svg).toHaveAttribute("data-color", "slate");
    expect(svg.getAttribute("aria-label")).toBe("golem, idle");
  });

  it("defaults to 32 px", () => {
    const { container } = render(<Sprite avatar="bard" color="gold" state="pending" />);
    expect(container.querySelector("svg")).toHaveAttribute("height", "32");
  });

  it("animates only under prefers-reduced-motion: no-preference, with frame 1 hidden otherwise", () => {
    const gated = css.slice(css.indexOf("@media (prefers-reduced-motion: no-preference)"));
    const open = css.slice(0, css.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(open).not.toContain("animation:");
    expect(open).toMatch(/\.sprite-frame\[data-frame="1"\]\s*\{\s*opacity:\s*0;?\s*\}/);
    expect(gated).toContain("@keyframes roster-step");
    expect(gated).toMatch(/animation:\s*roster-step\s+1\.2s\s+steps\(1, end\)\s+infinite/);
    expect(gated).toMatch(/animation-delay:\s*-0\.6s/);
  });

  it("gives the nameplate the pixel face", () => {
    expect(css).toMatch(/\n\.sprite-nameplate\s*\{[^}]*font-family:\s*var\(--font-pixel\)/);
  });
});
```

- [ ] **Step 3: Write the failing state mapping test**

`frontend/src/components/roster/__tests__/spriteState.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { LogEvent } from "@/lib/types";
import { stateFor, stateForNode } from "../spriteState";

const call = (id: string): LogEvent => ({ type: "tool_call", tool_name: "Edit", tool_id: id, summary: "" } as unknown as LogEvent);
const result = (id: string): LogEvent => ({ type: "tool_result", tool_id: id, content: "" } as unknown as LogEvent);
const text = (): LogEvent => ({ type: "text", text: "…" } as unknown as LogEvent);

describe("stateFor (a run)", () => {
  it("maps the terminal and waiting statuses directly", () => {
    expect(stateFor({ status: "pending", events: [] })).toBe("pending");
    expect(stateFor({ status: "completed", events: [call("1")] })).toBe("done");
    expect(stateFor({ status: "failed", events: [] })).toBe("failed");
  });
  it("is tool while the newest tool call has no result, thinking otherwise", () => {
    expect(stateFor({ status: "running", events: [] })).toBe("thinking");
    expect(stateFor({ status: "running", events: [call("1")] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), result("1")] })).toBe("thinking");
    expect(stateFor({ status: "running", events: [call("1"), result("1"), call("2")] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), text()] })).toBe("tool");
    expect(stateFor({ status: "running", events: [call("1"), result("1"), text()] })).toBe("thinking");
  });
});

describe("stateForNode (a graph node)", () => {
  it("maps the live node vocabulary, with ready and unknown as pending", () => {
    expect(stateForNode("pending")).toBe("pending");
    expect(stateForNode("ready")).toBe("pending");
    expect(stateForNode(undefined)).toBe("pending");
    expect(stateForNode("running")).toBe("thinking");
    expect(stateForNode("completed")).toBe("done");
    expect(stateForNode("failed")).toBe("failed");
  });
});
```

- [ ] **Step 4: Run the three test files to verify they fail**

Run: `npx vitest run src/components/roster`
Expected: FAIL with "Failed to resolve import" for each module.

- [ ] **Step 5: Author `sprites.ts`**

The shape, the compiler and the complete `golem` set are below; author the other eleven avatars to the same rules. Keep every frame sixteen rows of sixteen characters over `.` transparent, `o` outline, `f` fill, `h` highlight, `e` eye. The tests pin the structure, not the art; the art is yours, within the motif table.

```ts
import type { AvatarId } from "@/lib/agentIdentity";

/**
 * The roster (spec 3.2): twelve avatars as 16x16 character grids, one
 * Record<SpriteState, Frame[]> each, compiled once at module load into one
 * SVG path per character class so a rendered frame is at most four paths.
 * Not part of the divine layer (divine/CLAUDE.md rule 9): sprites may sit on
 * live cards and lists, step no faster than two frames per second, and the
 * source of this file stays under 40 KB (sprites.test.ts).
 */

export type SpriteState = "idle" | "thinking" | "tool" | "done" | "failed" | "pending";
export type CharClass = "o" | "f" | "h" | "e";
export type Frame = readonly string[];
export type CompiledFrame = Partial<Record<CharClass, string>>;

export const SPRITE_STATES: readonly SpriteState[] = ["idle", "thinking", "tool", "done", "failed", "pending"];
export const FRAME_COUNTS: Record<SpriteState, 1 | 2> = { idle: 2, thinking: 2, tool: 2, done: 1, failed: 1, pending: 1 };

/** Run-length rectangles, one path per class: "M<x> <y>h<len>v1h-<len>z" per run. */
export function compileFrame(frame: Frame): CompiledFrame {
  const out: Partial<Record<CharClass, string[]>> = {};
  frame.forEach((row, y) => {
    let x = 0;
    while (x < 16) {
      const c = row[x];
      if (c === ".") { x++; continue; }
      let len = 1;
      while (x + len < 16 && row[x + len] === c) len++;
      (out[c as CharClass] ??= []).push(`M${x} ${y}h${len}v1h-${len}z`);
      x += len;
    }
  });
  const paths: CompiledFrame = {};
  for (const k of Object.keys(out) as CharClass[]) paths[k] = out[k]!.join("");
  return paths;
}
```

The golem, the default avatar, complete. Rows are sixteen characters each.

```ts
const golem: Record<SpriteState, Frame[]> = {
  idle: [
    [
      "................",
      ".....oooooo.....",
      "....ohfffffo....",
      "....ofeffefo....",
      "....offffffo....",
      ".....offffo.....",
      "...ooofffoooo...",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..ooooffffoooo..",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
    [
      ".....oooooo.....",
      "....ohfffffo....",
      "....ofeffefo....",
      "....offffffo....",
      ".....offffo.....",
      "...ooofffoooo...",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..ooooffffoooo..",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
      "................",
    ],
  ],
  thinking: [
    [
      "...........h....",
      ".....oooooo.....",
      "....ohfffffo....",
      "....offffffo....",
      "....offffffo....",
      ".....offffo.....",
      "...ooofffoooo...",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..ooooffffoooo..",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
    [
      "............h...",
      ".....oooooo.....",
      "....ohfffffo....",
      "....ofeffefo....",
      "....offffffo....",
      ".....offffo.....",
      "...ooofffoooo...",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..ooooffffoooo..",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
  ],
  tool: [
    [
      "................",
      ".....oooooo.offo",
      "....ohfffffooffo",
      "....ofeffefo.ffo",
      "....offffffo.ffo",
      ".....offffo..ffo",
      "...ooofffooooffo",
      "..offoffffoffffo",
      "..offoffffoooo..",
      "..offoffffo.....",
      "..ooooffffoooo..",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
    [
      "................",
      ".....oooooo.....",
      "....ohfffffo....",
      "....ofeffefo....",
      "....offffffo....",
      ".....offffo.....",
      "...ooofffoooo...",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..ooooffffoooo..",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
  ],
  done: [
    [
      "offo........offo",
      "offo.oooooo.offo",
      "offoohfffffooffo",
      "offoofeffefooffo",
      "offoofffhffooffo",
      "offooooffffooffo",
      ".ooooofffoooooo.",
      "...ooffffffoo...",
      ".....offffo.....",
      ".....offffo.....",
      ".....offffo.....",
      ".....offffo.....",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
  ],
  failed: [
    [
      "................",
      "................",
      ".....oooooo.....",
      "....offffffo....",
      "....oefffffo....",
      "....offffffo....",
      ".....offffo.....",
      "...ooofffoooo...",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..offoffffoffo..",
      "..ooooffffoooo..",
      ".....offffo.....",
      "....offooffo....",
      "....offooffo....",
      "....oooooooo....",
    ],
  ],
  pending: [
    [
      "................",
      ".....oooooo.....",
      "....o......o....",
      "....o......o....",
      "....o......o....",
      ".....o....o.....",
      "...ooo....oooo..",
      "..o..o....o..o..",
      "..o..o....o..o..",
      "..o..o....o..o..",
      "..oooo....oooo..",
      ".....o....o.....",
      ".....o....o.....",
      "....o..oo..o....",
      "....o..oo..o....",
      "....oooooooo....",
    ],
  ],
};
```

A seventeen-character row fails the test; count before you commit.

State rules every avatar follows (the tests pin only the structure; a reviewer checks these by eye):

| State | Frames | Rule |
|---|---|---|
| idle | 2 | frame 1 is frame 0 shifted up one row (a bob) |
| thinking | 2 | eyes closed on frame 0 (`e` becomes `f`) and a one-cell `h` spark above the head that moves one cell between frames |
| tool | 2 | the right arm raised holding the avatar's implement on frame 0, at rest on frame 1 |
| done | 1 | both arms up, an extra `h` on the chest |
| failed | 1 | body shifted down one row, one eye becomes `f`, arms down |
| pending | 1 | outline only: every `f`, `h` and `e` cell of idle frame 0 becomes `.` |

Motifs, one per `AVATARS` id, in roster order: `seer` hooded figure with one large eye; `smith` square jaw, hammer; `warden` helmet, shield; `alchemist` round flask held up; `herald` horn; `arbiter` scales; `scholar` open book; `oracle` orb held in both hands; `engineer` wrench; `rogue` hood and dagger; `bard` lute; `golem` the blocky figure above. Readability at 16 px matters more than detail: a two-cell outline around a filled body, eyes two cells apart.

Then:

```ts
export const SPRITES: Record<AvatarId, Record<SpriteState, Frame[]>> = {
  seer, smith, warden, alchemist, herald, arbiter, scholar, oracle, engineer, rogue, bard, golem,
};

function compileAll(): Record<AvatarId, Record<SpriteState, CompiledFrame[]>> {
  const out = {} as Record<AvatarId, Record<SpriteState, CompiledFrame[]>>;
  for (const avatar of Object.keys(SPRITES) as AvatarId[]) {
    const states = {} as Record<SpriteState, CompiledFrame[]>;
    for (const state of SPRITE_STATES) states[state] = SPRITES[avatar][state].map(compileFrame);
    out[avatar] = states;
  }
  return out;
}

/** Compiled once at module load; Sprite reads this, never SPRITES. */
export const COMPILED = compileAll();
```

- [ ] **Step 6: Write `roster.css`**

```css
/* The roster (spec 3.2). Sprites are not the divine layer (divine/CLAUDE.md
   rule 9): they keep rendering under data-divine="off", obey reduced
   motion, and step at 1.2 s per two-frame cycle. */

.sprite {
  display: inline-block;
  vertical-align: middle;
  image-rendering: pixelated;
}

/* Frame 1 is hidden unless the animation below is allowed to show it. */
.sprite .sprite-frame[data-frame="1"] {
  opacity: 0;
}

/* The nameplate under a sprite: the third and last use of the pixel face
   (index.css owns .hud and .title-pixel). */
.sprite-nameplate {
  font-family: var(--font-pixel);
  font-size: 10px;
  line-height: 1.2;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

@media (prefers-reduced-motion: no-preference) {
  /* Two frames alternate: frame 0 shows for the first half of the cycle,
     frame 1 (delayed by half a cycle) for the second. One animated property. */
  @keyframes roster-step {
    0%, 49.99% { opacity: 1; }
    50%, 100% { opacity: 0; }
  }
  .sprite[data-frames="2"] .sprite-frame {
    animation: roster-step 1.2s steps(1, end) infinite;
  }
  .sprite[data-frames="2"] .sprite-frame[data-frame="1"] {
    opacity: 1;
    animation-delay: -0.6s;
  }
}
```

- [ ] **Step 7: Write `Sprite.tsx`**

```tsx
import type { CSSProperties } from "react";
import { DEFAULT_AVATAR, DEFAULT_COLOR, isAvatarId, isPaletteId } from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";
import { COMPILED, type CharClass, type SpriteState } from "./sprites";
import "./roster.css";

export interface SpriteProps {
  /** A roster id; anything else renders golem (spec 3.10). */
  avatar: string;
  /** A palette id; anything else renders slate. */
  color: string;
  state: SpriteState;
  size?: 16 | 32 | 48 | 64;
  /** aria-label; defaults to "<avatar>, <state>". Pass the display name. */
  label?: string;
  className?: string;
}

const FILL: Record<CharClass, string> = {
  o: "var(--color-background)",
  f: "var(--sprite-fill)",
  h: "color-mix(in srgb, var(--sprite-fill) 70%, white)",
  e: "var(--color-foreground)",
};
const ORDER: CharClass[] = ["o", "f", "h", "e"];

/** One avatar in one state: a 16x16 SVG, tinted by the palette token, stepping between two frames by CSS. */
export function Sprite({ avatar, color, state, size = 32, label, className }: SpriteProps) {
  const av = isAvatarId(avatar) ? avatar : DEFAULT_AVATAR;
  const col = isPaletteId(color) ? color : DEFAULT_COLOR;
  const frames = COMPILED[av][state];
  return (
    <svg
      role="img"
      aria-label={label ?? `${av}, ${state}`}
      viewBox="0 0 16 16"
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className={cn("sprite shrink-0", className)}
      data-avatar={av}
      data-color={col}
      data-state={state}
      data-frames={frames.length}
      style={{ "--sprite-fill": `var(--color-palette-${col})` } as CSSProperties}
    >
      {frames.map((paths, i) => (
        <g key={i} className="sprite-frame" data-frame={i}>
          {ORDER.filter((k) => paths[k]).map((k) => (
            <path key={k} d={paths[k]} fill={FILL[k]} />
          ))}
        </g>
      ))}
    </svg>
  );
}
```

- [ ] **Step 8: Write `spriteState.ts` and `index.ts`**

`spriteState.ts`:

```ts
import type { AgentState } from "@/hooks/useAgentStates";
import type { NodeLiveState } from "@/lib/pipeline-live";
import type { SpriteState } from "./sprites";

/**
 * The sprite state of one agent run (spec 3.2): a running agent whose
 * newest tool call has no result yet is using a tool; any other running
 * agent is thinking. Events are scanned from the end, so cost is bounded by
 * the current tool call's distance from the tail.
 */
export function stateFor(agent: Pick<AgentState, "status" | "events">): SpriteState {
  switch (agent.status) {
    case "pending": return "pending";
    case "completed": return "done";
    case "failed": return "failed";
  }
  const answered = new Set<string>();
  for (let i = agent.events.length - 1; i >= 0; i--) {
    const e = agent.events[i];
    if (e.type === "tool_result") answered.add(e.tool_id);
    else if (e.type === "tool_call") return answered.has(e.tool_id) ? "thinking" : "tool";
  }
  return "thinking";
}

/** The sprite state of a live graph node; the node vocabulary has no tool signal, so running is thinking. */
export function stateForNode(state: NodeLiveState["state"] | undefined): SpriteState {
  switch (state) {
    case "running": return "thinking";
    case "completed": return "done";
    case "failed": return "failed";
    default: return "pending";
  }
}
```

`index.ts`:

```ts
export { Sprite, type SpriteProps } from "./Sprite";
export { COMPILED, FRAME_COUNTS, SPRITES, SPRITE_STATES, compileFrame } from "./sprites";
export type { CharClass, CompiledFrame, Frame, SpriteState } from "./sprites";
export { stateFor, stateForNode } from "./spriteState";
```

- [ ] **Step 9: Rule 9 and the glossary**

Append to `frontend/src/components/divine/CLAUDE.md`, before the closing `---` paragraph:

```markdown
## 9. The roster is not the divine layer

The pixel sprites in `frontend/src/components/roster/` are the agents' faces, not embry0's identity, and rule 5 does not apply to them: a sprite may sit on a live card, a list row or a graph node. What does apply: every sprite animation is gated by `prefers-reduced-motion: no-preference` (reduced motion shows the first frame, static), a sprite steps no faster than two frames per second, carries no second animation, and there is one sprite per card or node. Rule 1's byte budget does not cover the roster; its own budget is 40 KB of `sprites.ts` source, pinned by `sprites.test.ts`. Sprites keep rendering under `data-divine="off"`.
```

Add to `docs/glossary.md` after the `### Browser Backend` entry (before `### Output`):

```markdown
### Roster
The twelve pixel avatars an agent can wear (`avatar` on its row; ids in `frontend/src/lib/agentIdentity.ts`), drawn as 16x16 character grids in `frontend/src/components/roster/sprites.ts` and compiled to SVG paths at load. Tinted by the agent's palette `color`. Not part of the [divine layer](#divine-layer) (its CLAUDE.md rule 9).

### Sprite
One roster avatar in one state (`idle`, `thinking`, `tool`, `done`, `failed`, `pending`), rendered by `<Sprite>` and stepped between two frames by one CSS keyframe under `prefers-reduced-motion: no-preference`. `stateFor` maps an agent run to a state, `stateForNode` a live graph node. Every surface that shows an agent shows its sprite (agents list and form, console cards, job page, [PartyNode](#partynode)).
```

- [ ] **Step 10: Run the roster tests and the gate**

Run: `npx vitest run src/components/roster`
Expected: PASS, all three files.
Run: `npm run build && npm run lint && npm test`
Expected: clean. (`userFacingText.test.ts`, if Task 1 has landed, now sees `.sprite-nameplate` as an allowed owner.)

- [ ] **Step 11: Commit**

```bash
git add frontend/src/components/roster frontend/src/components/divine/CLAUDE.md docs/glossary.md
git commit -F - <<'EOF'
feat(frontend): the roster, twelve pixel sprites stepped by CSS

An agent on the dashboard is a tinted initial. The roster gives each
avatar id a 16x16 pixel figure in six states, drawn as character grids
in code, compiled once into one SVG path per colour class, tinted by
the agent's palette token and stepped between two frames by a single
CSS keyframe under prefers-reduced-motion: no-preference. No image
files, no runtime dependency, under 40 KB of source.

stateFor maps an agent run (tool call open, thinking, done, failed,
pending) and stateForNode a live graph node onto a sprite state. Rule 9
in the divine CLAUDE.md says the roster is not the divine layer and
what still binds it.

Spec: docs/superpowers/specs/2026-10-18-game-ui-design.md, 3.2 (I).

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 3: Identity everywhere (issue J)

**Files:**
- Modify: `frontend/src/components/agents/AgentAvatar.tsx`
- Modify: `frontend/src/components/agents/__tests__/AgentAvatar.test.tsx`
- Modify: `frontend/src/components/agents/AgentIdentityFields.tsx`
- Create: `frontend/src/components/agents/__tests__/AgentIdentityFields.test.tsx`
- Modify: `frontend/src/pages/AgentsPage.tsx:124`
- Modify: `frontend/src/hooks/useLiveJobSummary.ts`
- Modify: `frontend/src/hooks/__tests__/useLiveJobSummary.test.ts`
- Modify: `frontend/src/components/console/RunningCard.tsx`
- Modify: `frontend/src/components/console/QueuedCard.tsx`
- Modify: `frontend/src/components/console/DoneFailedCard.tsx`
- Create: `frontend/src/components/console/firstPartyMember.ts`
- Modify: `frontend/src/components/console/__tests__/fixtures.ts`
- Modify: `frontend/src/components/console/__tests__/RunningCard.test.tsx`, `QueuedCard.test.tsx`, `DoneFailedCard.test.tsx`
- Create: `frontend/src/components/console/__tests__/firstPartyMember.test.ts`

**Interfaces:**
- Consumes: `Sprite`, `SpriteState` from `@/components/roster`; `identityOf`, `AVATARS`, `PALETTE`, `PALETTE_HEX` from `@/lib/agentIdentity`; `useAgentIdentity` from `@/hooks/useAgentIdentity`; `useTemplates`, `useTemplate` from `@/hooks/usePipelines`; `isSuccessfulTerminal` from `@/lib/jobStatus`.
- Produces: `AgentAvatar` keeps its props (`identity`, `size`, `className`) and attributes (`data-avatar`, `data-color`, `title`, `aria-label`) and gains `state?: SpriteState` (default `idle`). `UseLiveJobSummaryResult.toolOpen: boolean`. `firstPartyMember(graph: PipelineGraph | undefined): string` (an agent type).

- [ ] **Step 1: Write the failing tests**

Replace `frontend/src/components/agents/__tests__/AgentAvatar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { identityOf } from "@/lib/agentIdentity";
import { AgentAvatar } from "../AgentAvatar";

describe("AgentAvatar", () => {
  it("renders the agent's sprite with the roster id, the palette id and the display name", () => {
    render(<AgentAvatar identity={identityOf({ type: "developer" })} />);
    const tile = screen.getByTitle("Developer");
    expect(tile).toHaveAttribute("data-avatar", "smith");
    expect(tile).toHaveAttribute("data-color", "amber");
    const svg = tile.querySelector("svg")!;
    expect(svg).toHaveAttribute("data-avatar", "smith");
    expect(svg).toHaveAttribute("data-state", "idle");
    expect(svg).toHaveAttribute("aria-label", "Developer, idle");
    expect(tile.textContent).toBe("");
  });

  it("passes the state and size through", () => {
    render(<AgentAvatar identity={identityOf("qa")} state="tool" size={48} />);
    const svg = screen.getByTitle("QA").querySelector("svg")!;
    expect(svg).toHaveAttribute("data-state", "tool");
    expect(svg).toHaveAttribute("width", "48");
  });

  it("rounds an odd size down to the nearest sprite size", () => {
    render(<AgentAvatar identity={identityOf("qa")} size={40} />);
    expect(screen.getByTitle("QA").querySelector("svg")).toHaveAttribute("width", "32");
  });
});
```

`frontend/src/components/agents/__tests__/AgentIdentityFields.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentIdentityFields } from "../AgentIdentityFields";

const value = { display_name: "Release Bot", tagline: "Ships it.", avatar: "rogue" as const, color: "cyan" as const };

describe("AgentIdentityFields", () => {
  it("offers the twelve avatars as a sprite grid, in the chosen colour, with the current one checked", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const grid = screen.getByRole("radiogroup", { name: "Avatar" });
    const options = grid.querySelectorAll('[role="radio"]');
    expect(options).toHaveLength(12);
    expect(grid.querySelector('[role="radio"][aria-checked="true"]')).toHaveAttribute("aria-label", "rogue");
    for (const svg of grid.querySelectorAll("svg")) expect(svg).toHaveAttribute("data-color", "cyan");
    expect(screen.queryByRole("combobox", { name: /avatar/i })).toBeNull();
  });

  it("reports a chosen avatar", async () => {
    const onChange = vi.fn();
    render(<AgentIdentityFields type="release-bot" value={value} onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "bard" }));
    expect(onChange).toHaveBeenCalledWith({ avatar: "bard" });
  });

  it("previews the chosen avatar and colour at 48 px", () => {
    render(<AgentIdentityFields type="release-bot" value={value} onChange={() => {}} />);
    const preview = screen.getByTitle("Release Bot").querySelector("svg")!;
    expect(preview).toHaveAttribute("data-avatar", "rogue");
    expect(preview).toHaveAttribute("data-color", "cyan");
    expect(preview).toHaveAttribute("width", "48");
  });
});
```

Add to `frontend/src/hooks/__tests__/useLiveJobSummary.test.ts`, inside the `describe("useLiveJobSummary")`, using the file's `MockWebSocket`, `flushConnect` and `flushBatch` helpers:

```ts
  it("reports toolOpen while the newest tool call has no result", async () => {
    const { result } = renderHook(() => useLiveJobSummary("job-abc"));
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
    });
    expect(result.current.toolOpen).toBe(false);

    await act(async () => {
      lastCreatedWs!.simulateMessage({ type: "tool_call", timestamp: "t1", node: "developer", tool_name: "Edit", tool_id: "a", summary: "x.ts" });
    });
    await flushBatch();
    expect(result.current.toolOpen).toBe(true);

    await act(async () => {
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "Editing" });
    });
    await flushBatch();
    expect(result.current.toolOpen).toBe(true);

    await act(async () => {
      lastCreatedWs!.simulateMessage({ type: "tool_result", timestamp: "t3", node: "developer", tool_id: "a", content: "ok" });
    });
    await flushBatch();
    expect(result.current.toolOpen).toBe(false);

    await act(async () => {
      lastCreatedWs!.simulateMessage({ type: "tool_call", timestamp: "t4", node: "developer", tool_name: "Bash", tool_id: "b", summary: "npm test" });
      lastCreatedWs!.simulateMessage({ type: "node_started", timestamp: "t5", node: "review", attempt: 1 });
    });
    await flushBatch();
    expect(result.current.toolOpen).toBe(false);
  });
```

The first `it` in that `describe` lists every field of the empty result; add `expect(result.current.toolOpen).toBe(false);` to it.

`frontend/src/components/console/__tests__/firstPartyMember.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { PipelineGraph } from "@/lib/types";
import { firstPartyMember } from "../firstPartyMember";

const graph = (nodes: PipelineGraph["nodes"], edges: PipelineGraph["edges"] = []): PipelineGraph => ({
  graph_id: "g", name: "g", nodes, edges, metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "manual" },
});
const node = (id: string, type: string, x: number): PipelineGraph["nodes"][number] => ({ node_id: id, agent_type: type, position: { x, y: 0 } });

describe("firstPartyMember", () => {
  it("is the target of the edge out of the start sentinel", () => {
    const g = graph([node("s", "__start__", 0), node("r", "research", 300), node("d", "developer", 100)], [
      { edge_id: "e1", source: "s", target: "r", edge_type: "flow" },
    ]);
    expect(firstPartyMember(g)).toBe("research");
  });
  it("is the leftmost agent node when no sentinel edge exists", () => {
    expect(firstPartyMember(graph([node("r", "research", 300), node("d", "developer", 100)]))).toBe("developer");
  });
  it("is triage when there is no graph or no agent node", () => {
    expect(firstPartyMember(undefined)).toBe("triage");
    expect(firstPartyMember(graph([node("s", "__start__", 0), node("e", "__end__", 10)]))).toBe("triage");
  });
});
```

Console card tests. Extend `fixtures.ts`: `makeSummary` gains `toolOpen: false`. In `RunningCard.test.tsx` add:

```tsx
  describe("sprite", () => {
    it("shows the stage's sprite thinking, and using a tool while a call is open", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ currentNode: "developer", toolOpen: false }));
      const { unmount } = renderCard({ job: makeJob() });
      let sprite = screen.getByTestId("stage-sprite").querySelector("svg")!;
      expect(sprite).toHaveAttribute("data-avatar", "smith");
      expect(sprite).toHaveAttribute("data-state", "thinking");
      expect(sprite).toHaveAttribute("width", "32");
      unmount();
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ currentNode: "developer", toolOpen: true }));
      renderCard({ job: makeJob() });
      sprite = screen.getByTestId("stage-sprite").querySelector("svg")!;
      expect(sprite).toHaveAttribute("data-state", "tool");
    });
    it("shows no sprite before any stage is known", () => {
      renderCard({ job: makeJob({ current_stage: null }) });
      expect(screen.queryByTestId("stage-sprite")).toBeNull();
    });
  });
```

`QueuedCard.test.tsx` (mock `@/hooks/usePipelines` with `useTemplates` returning `{ data: [{ template_id: "t1", name: "qa", description: "", created_at: "" }] }` and `useTemplate` returning a graph whose start edge points at a `qa` node when called with `"t1"`, `{ data: undefined }` otherwise; mock `@/hooks/useAgents` as `RunningCard.test.tsx` does, with `qa` and `triage` rows):

```tsx
  it("shows the template's first party member waiting", () => {
    renderCard({ job: makeJob({ status: "pending", pipeline_template: "qa" }) });
    const sprite = screen.getByTestId("party-sprite").querySelector("svg")!;
    expect(sprite).toHaveAttribute("data-avatar", "alchemist");
    expect(sprite).toHaveAttribute("data-state", "pending");
  });
  it("shows triage waiting when the template is unknown or unset", () => {
    renderCard({ job: makeJob({ status: "pending", pipeline_template: null }) });
    expect(screen.getByTestId("party-sprite").querySelector("svg")).toHaveAttribute("data-avatar", "seer");
    cleanup();
    renderCard({ job: makeJob({ status: "pending", pipeline_template: "no-such-template" }) });
    expect(screen.getByTestId("party-sprite").querySelector("svg")).toHaveAttribute("data-avatar", "seer");
  });
```

`DoneFailedCard.test.tsx` (mock `@/hooks/useAgents` with a `review` row):

```tsx
  it("shows the last stage's sprite done for a successful job and failed otherwise", () => {
    renderCard({ job: makeJob({ status: "completed", current_stage: "reviewer", finished_at: "2026-07-08T11:00:00Z" }) });
    let sprite = screen.getByTestId("party-sprite").querySelector("svg")!;
    expect(sprite).toHaveAttribute("data-avatar", "warden");
    expect(sprite).toHaveAttribute("data-state", "done");
    cleanup();
    renderCard({ job: makeJob({ status: "failed", current_stage: "reviewer", finished_at: "2026-07-08T11:00:00Z" }) });
    sprite = screen.getByTestId("party-sprite").querySelector("svg")!;
    expect(sprite).toHaveAttribute("data-state", "failed");
  });
  it("shows no sprite for a legacy row without a recorded stage", () => {
    renderCard({ job: makeJob({ status: "completed", current_stage: null }) });
    expect(screen.queryByTestId("party-sprite")).toBeNull();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/components/agents src/components/console src/hooks/__tests__/useLiveJobSummary.test.ts`
Expected: FAIL. AgentAvatar renders text "D" and no svg; the form has a combobox; `toolOpen` undefined; `firstPartyMember` unresolved; `stage-sprite` and `party-sprite` absent.

- [ ] **Step 3: Make `AgentAvatar` a sprite**

```tsx
import { Sprite, type SpriteState } from "@/components/roster";
import type { AgentIdentity } from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";

interface AgentAvatarProps { identity: AgentIdentity; size?: number; state?: SpriteState; className?: string }

const SIZES = [64, 48, 32, 16] as const;

/** The agent's sprite (spec 3.3). The wrapper keeps the data attributes and title workstream 2 callers and tests read. */
export function AgentAvatar({ identity, size = 32, state = "idle", className }: AgentAvatarProps) {
  const spriteSize = SIZES.find((s) => s <= size) ?? 16;
  return (
    <span
      data-avatar={identity.avatar}
      data-color={identity.color}
      title={identity.displayName}
      className={cn("inline-flex shrink-0 items-center justify-center select-none", className)}
      style={{ width: size, height: size }}
    >
      <Sprite avatar={identity.avatar} color={identity.color} state={state} size={spriteSize} label={`${identity.displayName}, ${state}`} />
    </span>
  );
}
```

`AgentsPage.tsx:124`: `size={28}` becomes `size={32}` (the 28 px tile rounds down to a 16 px sprite; the list wants 32). No other change: the list already shows display name, tagline, model, provider and the skills on hover.

- [ ] **Step 4: The sprite grid in `AgentIdentityFields`**

Replace the `Select` (lines 65 to 78) with:

```tsx
          <div>
            <Label>Avatar</Label>
            <div role="radiogroup" aria-label="Avatar" className="mt-2 grid grid-cols-6 gap-1.5">
              {AVATARS.map((id) => {
                const selected = value.avatar === id;
                return (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    aria-label={id}
                    title={id}
                    onClick={() => onChange({ avatar: id })}
                    className={cn(
                      "flex items-center justify-center rounded-md border p-1",
                      selected ? "border-white bg-white/[0.06]" : "border-white/10 hover:border-white/30",
                    )}
                  >
                    <Sprite avatar={id} color={value.color} state="idle" size={32} label={id} />
                  </button>
                );
              })}
            </div>
          </div>
```

Import `Sprite` from `@/components/roster`; drop the `Select` import and the `AvatarId` cast. Change the preview to `<AgentAvatar identity={preview} size={48} className="mt-6" />`.

- [ ] **Step 5: `toolOpen` in `useLiveJobSummary`**

Add `toolOpen: boolean` to `UseLiveJobSummaryResult` (doc: "a tool call is in flight: the newest `tool_call` has no `tool_result` yet") and to `LiveSummarySnapshot` (`toolOpen: false` in `emptySnapshot`). Keep an `openToolIdsRef = useRef(new Set<string>())`; in `applyEvent`: on `tool_call` add `event.tool_id` and set `snapshot.toolOpen = true`; on `tool_result` delete `event.tool_id` and set `snapshot.toolOpen = openToolIdsRef.current.size > 0`; on `node_started` clear the set and set `false` (a new run starts with no call open). Reset the set in `onReset`. Return `toolOpen: summary.toolOpen`.

- [ ] **Step 6: The console cards**

`firstPartyMember.ts`:

```ts
import type { PipelineGraph } from "@/lib/types";

const SENTINELS = new Set(["__start__", "__end__"]);

/**
 * The agent type a template runs first (spec 3.3, QueuedCard): the target of
 * the edge out of the start sentinel, else the leftmost agent node, else
 * triage, where every default route begins.
 */
export function firstPartyMember(graph: PipelineGraph | undefined): string {
  if (!graph) return "triage";
  const byId = new Map(graph.nodes.map((n) => [n.node_id, n]));
  const start = graph.nodes.find((n) => n.agent_type === "__start__");
  const first = start && graph.edges.find((e) => e.source === start.node_id);
  const viaEdge = first ? byId.get(first.target) : undefined;
  if (viaEdge && !SENTINELS.has(viaEdge.agent_type)) return viaEdge.agent_type;
  const agents = graph.nodes.filter((n) => !SENTINELS.has(n.agent_type)).sort((a, b) => a.position.x - b.position.x);
  return agents[0]?.agent_type ?? "triage";
}
```

`RunningCard.tsx`: after `stageIdentity`, `const stageType = summary.currentNode ?? job.current_stage ?? null;`. Inside the first content row, before the stage badge:

```tsx
        {stageType && (
          <span data-testid="stage-sprite" className="shrink-0">
            <AgentAvatar identity={stageIdentity} size={32} state={summary.toolOpen ? "tool" : "thinking"} />
          </span>
        )}
```

`QueuedCard.tsx`:

```tsx
  const { data: templates } = useTemplates();
  const templateId = templates?.find((t) => t.name === job.pipeline_template)?.template_id ?? null;
  const { data: template } = useTemplate(templateId);
  const identity = useAgentIdentity(firstPartyMember(template?.graph));
```

and render `<span data-testid="party-sprite"><AgentAvatar identity={identity} size={32} state="pending" /></span>` at the start of the content row (the queue position text follows it).

`DoneFailedCard.tsx`:

```tsx
  const identity = useAgentIdentity(job.current_stage ?? "");
  const lastState: SpriteState = isSuccessfulTerminal(job.status) ? "done" : "failed";
  …
        {job.current_stage && (
          <span data-testid="party-sprite" className="shrink-0">
            <AgentAvatar identity={identity} size={32} state={lastState} />
          </span>
        )}
```

placed before the PR pill in the flex row.

- [ ] **Step 7: Run the tests and the gate**

Run: `npx vitest run src/components/agents src/components/console src/hooks/__tests__/useLiveJobSummary.test.ts src/pages/__tests__/AgentsPage.test.tsx src/pages/__tests__/AgentFormPage.test.tsx src/pages/__tests__/ConsolePage.test.tsx`
Expected: PASS. A page test that selected the avatar through `<select>` must now click the radio.
Run: `npm run build && npm run lint && npm test`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/agents frontend/src/components/console frontend/src/hooks frontend/src/pages/AgentsPage.tsx frontend/src/pages/__tests__
git commit -F - <<'EOF'
feat(frontend): sprites on the agents list, the form and the console

AgentAvatar, the seam workstream 2 left, renders the agent's sprite, so
every surface that showed the tinted initial shows the figure. The
agent form picks an avatar from a sprite grid in the chosen colour. The
console cards say who is working: the running card's stage sprite
thinks or uses a tool (useLiveJobSummary now reports toolOpen), the
queued card shows the template's first party member waiting, and the
finished card shows the last stage done or failed.

A legacy row without a recorded stage shows no sprite; a queued job
whose template the dashboard cannot resolve shows triage, where every
default route begins.

Spec: docs/superpowers/specs/2026-10-18-game-ui-design.md, 3.3 (J).

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 4: One node renderer and the party map (issue K)

**Files:**
- Create: `frontend/src/components/pipeline/PartyNode.tsx`
- Create: `frontend/src/components/pipeline/party.css`
- Modify: `frontend/src/components/pipeline/PipelineGraphView.tsx`
- Modify: `frontend/src/components/pipeline-editor/PipelineEditor.tsx:21-30` (import and `nodeTypes` only)
- Delete: `frontend/src/components/pipeline-editor/AgentNode.tsx`
- Modify: `frontend/src/components/pipeline-editor/circularLayout.ts:31-35`
- Modify: `frontend/src/components/pipeline-editor/__tests__/circularLayout.test.ts`
- Modify: `docs/glossary.md` (`### PartyNode` entry after `### Sprite`; `### AgentBar` says "sprite" for "avatar")
- Test: `frontend/src/components/pipeline/__tests__/PartyNode.test.tsx`
- Test: `frontend/src/components/pipeline/__tests__/PipelineGraphView.test.tsx`

**Interfaces:**
- Consumes: `Sprite`, `stateForNode` from `@/components/roster`; `useAgentIdentity` from `@/hooks/useAgentIdentity`; `useAgents` from `@/hooks/useAgents`; `effectiveSkills` from `@/lib/skills`; `deserializeGraph` from `@/lib/graph-utils`; `NodeLiveState` from `@/lib/pipeline-live`; `AlchemicalSigil` and `categoryToStage` are no longer used by a node (the sprite is the figure; the sentinels keep the sphere).
- Produces: `PartyNode: React.FC<NodeProps>` reading `data.agentType`, `data.label`, `data.model`, `data.skills` (a `NormalizedDelta | null`), `data.mode?: "editor" | "live"` (absent means editor) and in live mode `data.state`, `data.costUsd`, `data.turns`, `data.iteration`. Both graphs register `{ agentNode: PartyNode, startEndNode: StartEndNode }`. `PipelineGraphView` keeps its props.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/pipeline/__tests__/PartyNode.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import css from "../party.css?raw";

// Handle reads the node context React Flow provides; outside a canvas it has none.
vi.mock("@xyflow/react", async (importOriginal) => ({ ...(await importOriginal<typeof import("@xyflow/react")>()), Handle: () => null }));
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: ["superpowers:tdd", "superpowers:debug"] },
      { type: "review", display_name: "Reviewer", avatar: "warden", color: "rose", skills: [] },
    ],
  }),
}));
import { PartyNode } from "../PartyNode";

function props(data: Record<string, unknown>, selected = false): NodeProps {
  return { id: "n", data, selected, type: "agentNode", dragging: false, zIndex: 0, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true, draggable: true, selectable: true, width: 96, height: 96 } as unknown as NodeProps;
}
const wrap = render;

describe("PartyNode, editor mode", () => {
  it("shows the idle sprite, the nameplate and the effective skill count with the model", () => {
    wrap(<PartyNode {...props({ agentType: "developer", label: "developer", model: "claude-opus-4", skills: { add: ["superpowers:plan"], remove: ["superpowers:debug"] } })} />);
    const node = screen.getByTestId("party-node");
    expect(node).toHaveAttribute("data-mode", "editor");
    const sprite = node.querySelector("svg.sprite")!;
    expect(sprite).toHaveAttribute("data-avatar", "smith");
    expect(sprite).toHaveAttribute("data-state", "idle");
    expect(sprite).toHaveAttribute("width", "48");
    expect(node.querySelector(".sprite-nameplate")).toHaveTextContent("Developer");
    expect(node.querySelector(".hud")).toHaveTextContent("2 skills · opus");
    expect(node.querySelector("[data-ring]")).toHaveStyle({ borderColor: "#fbbf24" });
  });

  it("resolves the reviewer spelling to the review row", () => {
    wrap(<PartyNode {...props({ agentType: "reviewer", label: "reviewer", model: null, skills: null })} />);
    expect(screen.getByTestId("party-node").querySelector(".sprite-nameplate")).toHaveTextContent("Reviewer");
    expect(screen.getByTestId("party-node").querySelector(".hud")).toHaveTextContent("0 skills · defaults");
  });

  it("shows the slug and the golem for a type with no row", () => {
    wrap(<PartyNode {...props({ agentType: "release-bot", label: "release", model: null, skills: null })} />);
    const node = screen.getByTestId("party-node");
    expect(node.querySelector(".sprite-nameplate")).toHaveTextContent("release-bot");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-avatar", "golem");
  });
});

describe("PartyNode, live mode", () => {
  const live = (state: string, extra: Record<string, unknown> = {}) =>
    props({ agentType: "developer", label: "developer", mode: "live", state, costUsd: 1.5, turns: 12, iteration: 2, ...extra });

  it("maps the node state to the sprite and the ring", () => {
    const { unmount } = wrap(<PartyNode {...live("pending")} />);
    let node = screen.getByTestId("party-node");
    expect(node).toHaveAttribute("data-mode", "live");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-state", "pending");
    expect(node.querySelector("[data-ring]")).toHaveAttribute("data-ring", "pending");
    unmount();
    wrap(<PartyNode {...live("running")} />);
    node = screen.getByTestId("party-node");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-state", "thinking");
    expect(node.querySelector("[data-ring]")).toHaveAttribute("data-ring", "running");
  });

  it("writes attempt, cost and turns on the HUD line", () => {
    wrap(<PartyNode {...live("completed")} />);
    const node = screen.getByTestId("party-node");
    expect(node.querySelector(".hud")).toHaveTextContent("#2 · $1.50 · 12 turns");
    expect(node.querySelector("svg.sprite")).toHaveAttribute("data-state", "done");
    expect(node.querySelector("[data-ring]")).toHaveAttribute("data-ring", "completed");
  });

  it("shows failed as failed", () => {
    wrap(<PartyNode {...live("failed")} />);
    expect(screen.getByTestId("party-node").querySelector("svg.sprite")).toHaveAttribute("data-state", "failed");
  });
});

describe("party.css", () => {
  it("flashes the ring on completion only under prefers-reduced-motion: no-preference", () => {
    const gatedAt = css.indexOf("@media (prefers-reduced-motion: no-preference)");
    expect(gatedAt).toBeGreaterThan(-1);
    expect(css.slice(0, gatedAt)).not.toContain("animation:");
    expect(css.slice(gatedAt)).toMatch(/\[data-ring="completed"\]\s*\{\s*animation:\s*scale-in/);
  });
  it("hard-codes no hex colour", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
```

`frontend/src/components/pipeline/__tests__/PipelineGraphView.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import source from "../PipelineGraphView.tsx?raw";
import type { PipelineGraph } from "@/lib/types";
import { deriveNodeStates } from "@/lib/pipeline-live";
import { loadFixture } from "@/lib/__tests__/fixtures";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] }] }),
}));
import { PipelineGraphView, buildFlowData } from "../PipelineGraphView";

const graph: PipelineGraph = {
  graph_id: "g", name: "g", edges: [{ edge_id: "e", source: "s", target: "d", edge_type: "flow" }],
  nodes: [
    { node_id: "s", agent_type: "__start__", position: { x: 0, y: 0 } },
    { node_id: "d", agent_type: "developer", label: "developer", position: { x: 200, y: 0 } },
  ],
  metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "manual" },
};

describe("buildFlowData", () => {
  it("marks agent nodes live for PartyNode and keeps the sentinels on startEndNode", () => {
    const { nodes } = buildFlowData(graph, { d: { node_id: "d", agent_type: "developer", state: "running", iteration: 2, cost_usd: 0.2, turns: 3, duration_seconds: 4 } });
    const start = nodes.find((n) => n.id === "s")!;
    const dev = nodes.find((n) => n.id === "d")!;
    expect(start.type).toBe("startEndNode");
    expect(start.data).toEqual({ nodeRole: "start" });
    expect(dev.type).toBe("agentNode");
    expect(dev.data).toMatchObject({ agentType: "developer", mode: "live", state: "running", costUsd: 0.2, turns: 3, iteration: 2 });
    expect(dev.draggable).toBe(false);
  });

  it("gives a node no event touched the pending state", () => {
    const { nodes } = buildFlowData(graph, {});
    expect(nodes.find((n) => n.id === "d")!.data).toMatchObject({ state: "pending", costUsd: 0, turns: 0, iteration: 1 });
  });

  it("carries the states derived from the issue-to-pr fixture", () => {
    const events = loadFixture("issue-to-pr");
    const graphEvent = events.find((e) => e.type === "pipeline_graph");
    expect(graphEvent).toBeDefined();
    const fixtureGraph = (graphEvent as unknown as { graph: PipelineGraph }).graph;
    const { nodes } = buildFlowData(fixtureGraph, deriveNodeStates(fixtureGraph, events, "completed"));
    const states = Object.fromEntries(nodes.map((n) => [n.id, (n.data as { state: string }).state]));
    expect(Object.values(states)).toContain("completed");
    expect(Object.values(states)).not.toContain("ready");
    for (const n of nodes) expect(n.type).toBe("agentNode");
  });
});

describe("PipelineGraphView", () => {
  it("says it is waiting when there is no graph", () => {
    render(<PipelineGraphView graph={null} nodeStates={{}} />);
    expect(screen.getByText(/waiting for pipeline graph/i)).toBeInTheDocument();
  });

  it("keeps no renderer of its own and no hex colour", () => {
    expect(source).not.toContain("LiveAgentNode");
    expect(source).not.toMatch(/#[0-9a-fA-F]{6}\b/);
    expect(source).not.toContain("animate-pulse-glow");
  });
});
```

`circularLayout.test.ts`: replace every `type: "start"` / `type: "end"` test node with the real shape, `{ id, position, data: { nodeRole: "start" | "end" }, type: "startEndNode" }` (the `startNode`/`endNode` helpers at lines 189 to 200 and the inline nodes at 112 and 113), and add:

```ts
  it("detects the sentinels deserializeGraph produces, not a node type that never existed", async () => {
    const { deserializeGraph } = await import("@/lib/graph-utils");
    const { nodes } = deserializeGraph({
      graph_id: "g", name: "g", edges: [],
      nodes: [
        { node_id: "start", agent_type: "__start__", position: { x: 0, y: 0 } },
        { node_id: "t", agent_type: "triage", position: { x: 0, y: 0 } },
        { node_id: "d", agent_type: "developer", position: { x: 0, y: 0 } },
        { node_id: "end", agent_type: "__end__", position: { x: 0, y: 0 } },
      ],
      metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "manual" },
    });
    const result = circularLayout(nodes, []);
    expect(result.nodes.find((n) => n.id === "start")!.position).toEqual({ x: -CIRCLE_RADIUS * 1.4, y: 0 });
    expect(result.nodes.find((n) => n.id === "end")!.position).toEqual({ x: CIRCLE_RADIUS * 1.4, y: 0 });
    const legacy = { id: "x", position: { x: 0, y: 0 }, data: { label: "x" }, type: "start" } as Node;
    const r2 = circularLayout([node("t", "triage"), node("d", "developer"), legacy], []);
    expect(Math.abs(r2.nodes.find((n) => n.id === "x")!.position.x)).not.toBe(CIRCLE_RADIUS * 1.4);
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/components/pipeline src/components/pipeline-editor/__tests__/circularLayout.test.ts`
Expected: FAIL. `PartyNode` and `party.css` unresolved; the graph view source still contains `LiveAgentNode`; the sentinel test places `start` as an ordinary node.

- [ ] **Step 3: Fix `circularLayout.isSentinel`**

```ts
function isSentinel(node: Node): "start" | "end" | null {
  const role = (node.data as Record<string, unknown> | undefined)?.nodeRole;
  if (role === "start") return "start";
  if (role === "end") return "end";
  return null;
}
```

- [ ] **Step 4: Write `party.css` and `PartyNode.tsx`**

`party.css`:

```css
/* The party tile (spec 3.4): one renderer for the editor and the live graph. */

.party-ring {
  border-width: 2px;
  border-style: solid;
  border-radius: 9999px;
  transition: opacity 200ms ease-out;
}
.party-ring[data-ring="pending"] { opacity: 0.35; border-style: dashed; }
.party-ring[data-ring="editor"] { opacity: 0.6; }
.party-ring[data-ring="running"] { opacity: 1; }
.party-ring[data-ring="completed"] { opacity: 0.8; }
.party-ring[data-ring="failed"] { opacity: 0.9; border-color: var(--color-destructive) !important; }

@media (prefers-reduced-motion: no-preference) {
  /* One-shot on completion (divine rule 3's one-shot shape; the keyframes are index.css's scale-in). */
  .party-ring[data-ring="completed"] { animation: scale-in 0.3s ease-out both; }
}
```

`PartyNode.tsx`:

```tsx
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Sprite, stateForNode } from "@/components/roster";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { useAgents } from "@/hooks/useAgents";
import { resolveAgentType } from "@/lib/agentIdentity";
import type { NodeLiveState } from "@/lib/pipeline-live";
import { effectiveSkills, type NormalizedDelta } from "@/lib/skills";
import type { AgentDefinition } from "@/lib/types/agents";
import "./party.css";

const TILE_PX = 96;

/**
 * The one agent node (spec 3.4): a 96 px tile with the agent's sprite in the
 * state's frame, a ring in its palette colour, the display name on a pixel
 * nameplate and one HUD line. Editor mode (data.mode absent) shows the
 * effective skill count and the model; live mode shows attempt, cost and
 * turns and maps the node's live state onto the sprite and the ring. The
 * start and end sentinels stay on StartEndNode.
 */
export function PartyNode({ data, selected }: NodeProps) {
  const agentType = data.agentType as string;
  const identity = useAgentIdentity(agentType);
  const { data: agents } = useAgents();
  const mode = data.mode === "live" ? "live" : "editor";
  const liveState = data.state as NodeLiveState["state"] | undefined;
  const spriteState = mode === "live" ? stateForNode(liveState) : "idle";
  const ring = mode === "live" ? (liveState === "ready" ? "pending" : (liveState ?? "pending")) : "editor";

  let hud: string;
  if (mode === "live") {
    const cost = (data.costUsd as number | undefined) ?? 0;
    hud = `#${(data.iteration as number | undefined) ?? 1} · $${cost.toFixed(2)} · ${(data.turns as number | undefined) ?? 0} turns`;
  } else {
    const row = (agents as AgentDefinition[] | undefined)?.find((a) => a.type === resolveAgentType(agentType));
    const skills = effectiveSkills(row?.skills ?? [], data.skills as NormalizedDelta | null).length;
    const model = data.model as string | null | undefined;
    const shortModel = model ? model.replace("claude-", "").split("-")[0] : "defaults";
    hud = `${skills} skill${skills === 1 ? "" : "s"} · ${shortModel}`;
  }

  return (
    <div data-testid="party-node" data-mode={mode} className="flex flex-col items-center gap-1 select-none">
      <div
        className="relative transition-transform duration-200 hover:-translate-y-0.5"
        style={{ width: TILE_PX, height: TILE_PX, filter: selected ? `drop-shadow(0 0 12px ${identity.hex}66)` : undefined }}
      >
        <Handle type="target" position={Position.Left} className="!w-2 !h-2 !bg-card" style={{ border: `2px solid ${identity.hex}`, top: "50%" }} />
        <div data-ring={ring} className="party-ring absolute inset-1 flex items-center justify-center bg-card" style={{ borderColor: identity.hex }}>
          <Sprite avatar={identity.avatar} color={identity.color} state={spriteState} size={48} label={`${identity.displayName}, ${spriteState}`} />
        </div>
        <Handle type="source" position={Position.Right} className="!w-2 !h-2 !bg-card" style={{ border: `2px solid ${identity.hex}`, top: "50%" }} />
      </div>
      <div className="flex flex-col items-center min-w-0 max-w-[140px]">
        <div className="sprite-nameplate truncate w-full text-center" style={{ color: identity.hex }}>{identity.displayName}</div>
        {data.label && data.label !== identity.displayName && data.label !== agentType && (
          <div className="text-[11px] text-foreground/80 truncate w-full text-center">{data.label as string}</div>
        )}
        <div className="hud text-[10px] text-muted-foreground truncate w-full text-center">{hud}</div>
      </div>
    </div>
  );
}
```

`normalizeDelta(null)` is the identity, so a node without a skills override counts the row's own skills.

- [ ] **Step 5: Register `PartyNode` in both graphs; delete `AgentNode`**

`PipelineEditor.tsx`: replace `import { AgentNode } from "./AgentNode";` with `import { PartyNode } from "@/components/pipeline/PartyNode";` and line 30 with `const nodeTypes = { agentNode: PartyNode, startEndNode: StartEndNode };`.

`PipelineGraphView.tsx`: delete `LiveAgentNode` (lines 10 to 83) and the `useAgentIdentity` and `NodeProps` imports; import `PartyNode` from `./PartyNode` and `StartEndNode` from `@/components/pipeline-editor/StartEndNode`; `const nodeTypes = { agentNode: PartyNode, startEndNode: StartEndNode };`. Export `buildFlowData` (the test reads it; React Flow does not lay nodes out in jsdom). In it, keep each node's own `type` (sentinels stay `startEndNode`) and add `mode: "live"` to agent nodes only:

```ts
  const nodes: Node[] = baseNodes.map((n) =>
    n.type === "startEndNode"
      ? { ...n, draggable: false, selectable: false, connectable: false }
      : {
          ...n,
          draggable: false,
          selectable: true,
          connectable: false,
          data: {
            ...n.data,
            mode: "live",
            state: nodeStates[n.id]?.state ?? "pending",
            costUsd: nodeStates[n.id]?.cost_usd ?? 0,
            turns: nodeStates[n.id]?.turns ?? 0,
            durationSeconds: nodeStates[n.id]?.duration_seconds ?? 0,
            iteration: nodeStates[n.id]?.iteration ?? 1,
          },
        },
  );
```

The container `bg-[#0f1120]` becomes `bg-canvas`; `<Background color="#1e2040" …/>` becomes `<Background color="var(--color-grid)" …/>`.

`git rm frontend/src/components/pipeline-editor/AgentNode.tsx`. `tsc -b` finds any other importer (there is none on the branch at the time of writing).

- [ ] **Step 6: Glossary**

After the `### Sprite` entry:

```markdown
### PartyNode
The one agent node renderer (`frontend/src/components/pipeline/PartyNode.tsx`) the [PipelineEditor](#pipelineeditor) and the live job graph both register: the agent's [sprite](#sprite) inside a ring in its palette colour, the display name on a pixel nameplate, and one HUD line (skill count and model in the editor; attempt, cost and turns live). The start and end sentinels keep their gold sphere.
```

In `### AgentBar`, "Each chip shows the agent's avatar and display name" becomes "Each chip shows the agent's sprite and display name".

- [ ] **Step 7: Run the tests and the gate**

Run: `npx vitest run src/components/pipeline src/components/pipeline-editor`
Expected: PASS.
Run: `npm run build && npm run lint && npm test`
Expected: clean; `AgentDetailPopup.skills.test.tsx` and `autoLayout.test.ts` unchanged and green.

- [ ] **Step 8: Commit**

```bash
git add -A frontend/src/components/pipeline frontend/src/components/pipeline-editor docs/glossary.md
git commit -F - <<'EOF'
feat(frontend): one PartyNode for the editor and the live graph

The editor's AgentNode (gold sigil sphere) and the graph view's
LiveAgentNode shared nothing, down to the palette, so a party did not
look the same designed and running. PartyNode replaces both: the
agent's sprite in the state's frame inside a ring in its colour, the
display name on a pixel nameplate, and one HUD line (skill count and
model in the editor; attempt, cost and turns live). The sentinels keep
the gold sphere.

circularLayout detected sentinels by node.type "start"/"end", a shape
nothing produces (deserializeGraph writes type startEndNode with
data.nodeRole), so they were laid out as ordinary nodes; it reads
nodeRole now. The live view no longer overwrites a sentinel's type.

Spec: docs/superpowers/specs/2026-10-18-game-ui-design.md, 3.4 (K).

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

## Integration and PR

Branch `feat/game-ui` (worktree `/Users/tomas/repos/embry0-worktrees/game-ui`) already carries the spec commit; this plan is committed on it before Task 1 is dispatched. Task worktrees `gui-t1` to `gui-t4` under `/Users/tomas/repos/embry0-worktrees/`, branches `gui/t1` to `gui/t4`, each cut from the integration branch at its wave's start.

After each wave, on the integration branch: merge the wave's task branches (fast-forward or merge commit, no squash), grep the tree for conflict markers, grep the touched files for duplicate `function` and `export const` definitions, then the frontend gate (`npm run build && npm run lint && npm test` in `frontend/`) and the backend guards that read the docs and the frontend tree (`.venv/bin/python -m pytest tests/unit/docs tests/unit/test_user_facing_text.py -q`). The name denylist runs in CI (`scripts/check_denylist.py` with the private list); before pushing, read the diff once for a host, customer or person name.

After wave 1: a contact sheet of every avatar in every state (twelve rows, six states, 64 px, both frames of a two-frame state side by side), rendered from `COMPILED` into one HTML file in the session scratchpad and screenshotted. The sheet goes into the PR for the human review of the art; redraws land as a fix-wave task before merge.

After wave 2: the whole-branch review (Opus, `superpowers:requesting-code-review`), one fix wave, re-review, then `gh pr create` against `main` with the spec, the plan, the contact sheet and this summary in the body. Manual check before merging: the dashboard on a deployment with one running job (console cards and the job graph animate; sprites static under reduced motion in DevTools' rendering emulation), the agents list and form, and a template in the editor; screenshots in the PR.

## Deploy notes

Frontend only. On a deployment: `git pull --ff-only && cd infra && ./deploy.sh frontend`. No orchestrator rebuild, no migration, no compose change, no sandbox image rebuild. The public dashboard CSP already allows Google Fonts; the first load fetches one more face.
