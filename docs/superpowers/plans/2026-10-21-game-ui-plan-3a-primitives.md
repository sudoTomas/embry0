# Game UI, plan 3a of 3: primitives, the overlays on them, and merge decisions from the dashboard (issue O and spec 3.8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `components/ui/` gains `Dialog`, `Menu`, `Switch`, `Meter`, `Kbd` and a `Sprite` re-export; the six hand-rolled overlays this workstream owns (`AgentDetailPopup`, `EdgeDetailPopup`, `TemplateDrawer`, `TemplatePicker`, `CreateJobDialog`, `TranscriptDrawer`) move onto them, as do the one `role="switch"` and the budget meter; and `POST /merge-approvals/{id}/decide` accepts a decision from an admin on the dashboard, with no approval chat in the loop.

**Architecture:** `Dialog` is the one modal shell: it composes the existing `Overlay` (the portal and the only `fixed inset-0` box), `createFocusTrap` (Tab trap, focus in, focus back to the opener) and adds Escape, the backdrop click and the body scroll lock; its entrance keyframes live in `index.css` beside the rest of the motion. `Menu` is a button-anchored `role="menu"` list with arrow keys, Escape and outside-click. `Switch` and `Meter` lift the markup that `PullRequestReviewCard` and `JobHud` already carry so there is one of each. On the backend, `MergeApprovalService.decide` gains `via`, and the route tells the relay's body (`decided_by` and `chat_id`) from the dashboard's (neither): the dashboard form passes the admin gate and names the actor itself. Plan 3b (the surfaces of issues P and Q) builds on both.

**Tech Stack:** React 19, Tailwind v4 tokens from `index.css`, lucide icons, vitest + Testing Library + user-event, TypeScript 5.9; FastAPI + pydantic, asyncpg repositories, pytest (db-backed tests marked `requires_postgres`, mock-app tests with `create_app`). No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-game-ui-design.md`, sections 3.8, 3.9, 3.10 (reduced motion), 4, 5 and issue O of section 6. Plan 2 (`docs/superpowers/plans/2026-10-20-game-ui-plan-2-job-page.md`) built `TranscriptDrawer` and `JobHud` as hand-rolled shells "until plan 3's Dialog"; this plan is that plan. Issues P and Q are plan 3b.

**Deviation from the spec, stated:** the spec words 3.8 as "a sibling `POST /merge-approvals/{id}/decide` body form". FastAPI holds one handler per method and path, so the dashboard form is the same route with a body that carries `decision` alone; the relay's form (`decision`, `decided_by`, `chat_id`) is unchanged. A body with exactly one of `decided_by` and `chat_id` is 422.

## Global Constraints

- No new runtime dependency, no image files, no migration, no compose change, no sandbox image rebuild. Deploy is `infra/deploy.sh` (orchestrator for the route, frontend for everything else).
- **Motion policy** (`frontend/src/index.css`, the "Motion" comment block; `frontend/src/components/divine/CLAUDE.md` rule 3): only `transform` and `opacity` animate; every animation sits under `@media (prefers-reduced-motion: no-preference)`; keyframes live in `index.css`, never in a component `<style>`; no entrance uses `animation-fill-mode: forwards` (a finished transform makes the element the containing block of every `position: fixed` box inside it); no divine element on a dialog, a menu, a switch or a meter (rule 5).
- `Overlay` stays **the only `fixed inset-0` box** in `frontend/src` (`components/ui/__tests__/Overlay.test.tsx` scans every source). `Dialog` renders through `Overlay`; nothing else draws over the window.
- `createFocusTrap` (`frontend/src/lib/focus-trap.ts`) is the only focus trap. No file pairs it with React's `autoFocus` (`lib/__tests__/focus-trap.test.tsx` scans for that); a field that should take focus first carries `data-autofocus`.
- The pixel face stays behind exactly `.hud`, `.title-pixel` and `.sprite-nameplate` (plan 1's source-scanning test). Rule 9 of the divine CLAUDE.md holds: one sprite per card or node, never a second animation on it; nothing under `components/jobs/` or `components/logs/` carries `animate-pulse` or `animate-bounce` (`components/jobs/__tests__/noPulse.test.ts`).
- `frontend/src/lib/agentIdentity.ts` is the only identity source; `AgentDetailPopup` keeps `useAgentIdentity` and `AgentAvatar`.
- Accessible names stay what the tests pin: the agent popup is named by the agent (`aria-labelledby` its title), the edge popup by `"Flow edge from A to B"` or `"Feedback loop from A to B"`, the template picker's heading is `"Load pipeline template"`, the transcript drawer's name is `"Stored session of <name> for this job"`, the review switch is `"Review pull requests"`, the budget meter is `"Budget"`.
- The dashboard decision route is **admin-tier** (`ensure_admin`: an admin-scope key, the master key or an admin session); the relay's form keeps the router's default auth plus the `merge` scope allowance in `MERGE_SCOPE_MUTATIONS` (`embry0/api/deps.py:31`), which names this exact route template and must not change. `decided_by` for a dashboard decision is `request_actor(request)` (session email, key owner email, `key:<name>`, or the master-key actor), never a client-supplied name; the stored `chat_id` is the literal `dashboard`.
- Vendor-neutral naming: no customer, deployment, host or person name anywhere in the tree (`CONTRIBUTING.md`). Test fixtures use `acme/widgets`-style names and `ops@example.test`-style addresses.
- Commits follow `.claude/skills/commit-standard` and end with `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`. No AI co-author footers.
- **Frontend gate for every frontend task**, from `frontend/`: `npm run build`, `npm run lint` (0 errors; 4 warnings pre-exist), `npx vitest run` (read the summary line, not a grep).
- **Backend gate for Task 2**, from the worktree root with a venv that has the dev extras and a throwaway Postgres (`CLAUDE.md`, "The local test baseline is not green"; the server `embry0-test-pg` on `127.0.0.1:55432` is already running on this machine; give it a fresh password with `docker exec -i embry0-test-pg psql -U embry0 <<<"ALTER USER embry0 PASSWORD '$EMBRY0_TEST_PG_PASSWORD';"` and a private database with `docker exec embry0-test-pg createdb -U embry0 "$DB"`): `TEST_DATABASE_URL=... python -m pytest tests/unit/services/test_merge_approvals.py tests/unit/api/v1/test_merge_approvals_api.py tests/unit/api/v1/test_merge_decide_dashboard_gate.py tests/unit/api/test_merge_scope_auth.py tests/unit/api/test_route_table.py tests/unit/docs tests/unit/test_user_facing_text.py -q`, then `ruff check` and `ruff format --check` on the touched files and `mypy embry0`.
- Every new test is shown failing on the tree before its code (a clean `git archive` of the base commit, not a stash), with its own assertion message; report the RED and GREEN summary lines.

## Review Focus

Five inputs the spec implies but names no test for; each line's test is in the task that owns the code.

1. **Escape inside a control that owns Escape** (the template drawer's rename field, an open `Menu`): the control handles it and the `Dialog` around it stays open. Pinned in Task 1 (`Menu.test.tsx`, "closes on Escape and leaves the dialog around it open") and Task 3 (`TemplateDrawer.test.tsx`, "Escape in the rename field leaves rename mode and keeps the drawer open").
2. **Two dialogs in turn** (the template drawer closes by loading a template, then an agent popup opens): the body's scroll lock is released and re-taken, not left `hidden` after the second closes, and each restores the overflow value it found. Pinned in Task 1 (`Dialog.test.tsx`, "locks the body's scroll while open and gives back what it found").
3. **The relay's `merge`-scope key sending the dashboard form**, and a `read` key doing the same: both 403, and the relay's own form from the merge key still 200. Pinned in Task 2 (`test_merge_decide_dashboard_gate.py`).
4. **Cost over budget, and a budget of zero**: the meter's bar caps at 100 % and reports `over`; a zero or negative `max` draws an empty bar, never `NaN`. Pinned in Task 1 (`Meter.test.tsx`, "caps the bar at full and never divides by zero").
5. **A menu on a row that is itself a button** (the template drawer's cards load a template on click): opening the menu and choosing an item never reaches the row. Pinned in Task 1 (`Menu.test.tsx`, "keeps its clicks from the row behind it") and Task 3 (`TemplateDrawer.test.tsx`, "opens a row's menu without loading the template").

---

## Wave plan and file ownership

Two waves. Wave 1 runs Tasks 1 and 2 in parallel in per-task worktrees cut from `feat/game-ui-3a`; wave 2 runs Tasks 3 and 4 in parallel after wave 1 is integrated. A file is owned by exactly one task; a task that needs a change in another task's file asks the controller.

| Wave | Task | Owns (create, modify or delete) |
|---|---|---|
| 1 | 1 | `frontend/src/components/ui/{Dialog,Menu,Switch,Meter,Kbd,Sprite}.tsx`, their tests under `frontend/src/components/ui/__tests__/`, `frontend/src/index.css` (adds the three `dialog-in*` entrances; leaves `pipeline-popup-in` in place for Task 3) |
| 1 | 2 | `embry0/services/merge_approvals.py`, `embry0/api/v1/merge_approvals.py`, `tests/unit/services/test_merge_approvals.py` (append), `tests/unit/api/v1/test_merge_approvals_api.py` (append and one assertion), `tests/unit/api/v1/test_merge_decide_dashboard_gate.py` (create), `docs/merge-approvals.md` |
| 2 | 3 | `frontend/src/components/pipeline-editor/{AgentDetailPopup,EdgeDetailPopup,TemplatePicker,TemplateDrawer}.tsx`, `frontend/src/components/pipeline-editor/__tests__/popupEntrance.test.tsx` (rewrite), `frontend/src/components/pipeline-editor/__tests__/TemplateDrawer.test.tsx` (create), `frontend/src/index.css` (removes the `pipeline-popup-in` block), `docs/glossary.md` (TemplateDrawer entry) |
| 2 | 4 | `frontend/src/components/jobs/CreateJobDialog.tsx` (+ its test), `frontend/src/pages/JobsPage.tsx`, `frontend/src/components/logs/TranscriptDrawer.tsx`, `frontend/src/components/jobs/JobHud.tsx`, `frontend/src/components/environments/PullRequestReviewCard.tsx` |

Task 3 and Task 4 both import Task 1's primitives as they exist on the branch. `index.css` is touched by Task 1 (wave 1) and Task 3 (wave 2), never in the same wave.

---

### Task 1: The primitives (issue O, first half)

**Files:**
- Create: `frontend/src/components/ui/Dialog.tsx`, `frontend/src/components/ui/Menu.tsx`, `frontend/src/components/ui/Switch.tsx`, `frontend/src/components/ui/Meter.tsx`, `frontend/src/components/ui/Kbd.tsx`, `frontend/src/components/ui/Sprite.tsx`
- Create: `frontend/src/components/ui/__tests__/Dialog.test.tsx`, `Menu.test.tsx`, `Switch.test.tsx`, `Meter.test.tsx`, `Kbd.test.tsx`
- Modify: `frontend/src/index.css` (after the `/* ----- motion: pipeline editor ----- */` block, about line 402)

**Interfaces:**
- Consumes: `Overlay` (`@/components/ui/Overlay`), `createFocusTrap` (`@/lib/focus-trap`), `cn` (`@/lib/utils`), `Sprite` (`@/components/roster`), `X` and `MoreVertical` from `lucide-react`.
- Produces, for Tasks 3 and 4 and for plan 3b:
  - `Dialog({ open?, onClose, label?, labelledBy?, title?, side?, dismissOnBackdrop?, className?, style?, children })` with `type DialogSide = "center" | "right" | "left"`.
  - `Menu({ label, items, align?, children?, className?, triggerClassName? })` with `interface MenuItem { label: string; onSelect: () => void; icon?: ReactNode; destructive?: boolean; disabled?: boolean }`.
  - `Switch({ checked, onCheckedChange, label?, id?, disabled?, className? })`.
  - `Meter({ value, max, label, className? })`, `levelOf(value, max): MeterLevel`, `type MeterLevel = "ok" | "warn" | "over"`.
  - `Kbd(props: ComponentProps<"kbd">)`.
  - `Sprite` re-exported from `@/components/ui/Sprite`.
  - CSS classes `dialog-in`, `dialog-in-right`, `dialog-in-left`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/ui/__tests__/Dialog.test.tsx`:

```tsx
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import css from "../../../index.css?raw";
import { Dialog } from "../Dialog";

function sheetRules(): CSSRule[] {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const rules = Array.from(style.sheet?.cssRules ?? []);
  style.remove();
  return rules;
}

/** A page with an opener, so focus has somewhere to come from and go back to. */
function Page({ side }: { side?: "center" | "right" | "left" }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>Open</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Delete host" side={side}>
        <div className="p-5">
          <button type="button">Keep</button>
          <button type="button">Delete</button>
        </div>
      </Dialog>
    </div>
  );
}

describe("Dialog", () => {
  it("is a modal dialog over the window, named by its title", () => {
    render(<Dialog onClose={vi.fn()} title="Delete host">body</Dialog>);
    const dialog = screen.getByRole("dialog", { name: "Delete host" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("heading", { name: "Delete host" })).toBeInTheDocument();
    expect(dialog.parentElement?.parentElement, "the layer is a child of body (Overlay)").toBe(document.body);
    expect(dialog).toHaveClass("dialog-in");
  });

  it("renders nothing while closed", () => {
    render(<Dialog open={false} onClose={vi.fn()} title="Delete host">body</Dialog>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });

  it("a label outranks the title for the dialog's name", () => {
    render(<Dialog onClose={vi.fn()} label="Stored session of Developer for this job" title="Stored session">body</Dialog>);
    expect(screen.getByRole("dialog", { name: "Stored session of Developer for this job" })).toBeInTheDocument();
  });

  it("closes on Escape, on the backdrop and on Close, not on a click inside", () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} title="Delete host"><button type="button">Keep</button></Dialog>);
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(onClose, "a click inside the panel is not a dismissal").not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose, "the backdrop closes").toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("can refuse the backdrop", () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} title="Delete host" dismissOnBackdrop={false}>body</Dialog>);
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("moves focus in, keeps Tab inside, and gives focus back to the opener", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog");
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    const seen = new Set<Element | null>();
    for (let i = 0; i < 6; i++) {
      await user.tab();
      seen.add(document.activeElement);
    }
    expect([...seen].every((el) => dialog.contains(el)), "Tab never leaves the dialog").toBe(true);
    expect(seen.size, "Close, Keep and Delete are the three stops").toBe(3);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement, "focus returns to what opened the dialog").toBe(opener);
  });

  it("locks the body's scroll while open and gives back what it found", async () => {
    const user = userEvent.setup();
    document.body.style.overflow = "auto";
    render(<Page />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(document.body.style.overflow).toBe("hidden");
    await user.keyboard("{Escape}");
    expect(document.body.style.overflow, "the value before the dialog, not an empty string").toBe("auto");
    // A second dialog in turn locks and releases again.
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(document.body.style.overflow).toBe("hidden");
    await user.keyboard("{Escape}");
    expect(document.body.style.overflow).toBe("auto");
    document.body.style.overflow = "";
  });

  it("a drawer sits at the window's edge with no gutter", () => {
    render(<Dialog onClose={vi.fn()} title="Templates" side="left">body</Dialog>);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveClass("dialog-in-left", "h-full");
    expect(dialog.parentElement).toHaveClass("p-0", "justify-start");
    expect(dialog).not.toHaveClass("rounded-lg");
  });

  it("keeps its entrances in index.css, moving only transform and opacity, for people who did not ask for reduced motion", () => {
    const rules = sheetRules();
    for (const name of ["dialog-in", "dialog-in-right", "dialog-in-left"]) {
      const keyframes = rules.filter((r): r is CSSKeyframesRule => r instanceof CSSKeyframesRule && r.name === name);
      expect(keyframes, `one @keyframes ${name}`).toHaveLength(1);
      const props = Array.from(keyframes[0].cssRules).flatMap((f) => Array.from((f as CSSKeyframeRule).style));
      expect(props.length).toBeGreaterThan(0);
      expect(props.filter((p) => p !== "transform" && p !== "opacity"), `${name} animates only transform and opacity`).toEqual([]);
      const owners = rules.flatMap((rule) => {
        const inner = rule instanceof CSSMediaRule ? Array.from(rule.cssRules) : [rule];
        return inner
          .filter((r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === `.${name}`)
          .map(() => (rule instanceof CSSMediaRule ? rule.media.mediaText : "all"));
      });
      expect(owners, `.${name} runs only under the reduced-motion gate`).toEqual(["(prefers-reduced-motion: no-preference)"]);
      const fill = rules
        .flatMap((rule) => (rule instanceof CSSMediaRule ? Array.from(rule.cssRules) : [rule]))
        .filter((r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === `.${name}`)
        .map((r) => r.style.animationFillMode || r.style.animation);
      expect(fill.join(" "), "no forwards fill: a kept transform would become a containing block").not.toMatch(/forwards|both/);
    }
  });
});
```

`frontend/src/components/ui/__tests__/Menu.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Dialog } from "../Dialog";
import { Menu } from "../Menu";

function items(onSelect = vi.fn()) {
  return [
    { label: "Rename", onSelect: () => onSelect("Rename") },
    { label: "Duplicate", onSelect: () => onSelect("Duplicate") },
    { label: "Delete", onSelect: () => onSelect("Delete"), destructive: true },
  ];
}

describe("Menu", () => {
  it("opens a named menu from its button and focuses the first item", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions for quick-fix" items={items()} />);
    const trigger = screen.getByRole("button", { name: "Actions for quick-fix" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    const menu = screen.getByRole("menu", { name: "Actions for quick-fix" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", menu.id);
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Rename", "Duplicate", "Delete"]);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Rename" })));
    expect(screen.getByRole("menuitem", { name: "Delete" })).toHaveClass("text-destructive");
  });

  it("selects with a click, closes and hands focus back to its button", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Menu label="Actions" items={items(onSelect)} />);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(onSelect).toHaveBeenCalledWith("Duplicate");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Actions" }));
  });

  it("moves with the arrow keys and closes on Escape, leaving the dialog around it open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog onClose={onClose} title="Templates">
        <Menu label="Actions" items={items()} />
      </Dialog>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Rename" })));
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" }));
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement, "the arrows wrap").toBe(screen.getByRole("menuitem", { name: "Rename" }));
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Actions" }));
    expect(onClose, "Escape closed the menu, not the dialog").not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes on a press outside without selecting", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <div>
        <button type="button">Elsewhere</button>
        <Menu label="Actions" items={items(onSelect)} />
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.pointerDown(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("keeps its clicks from the row behind it", async () => {
    const user = userEvent.setup();
    const row = vi.fn();
    render(
      <div onClick={row}>
        <Menu label="Actions" items={items()} />
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(row, "neither the trigger nor an item click reaches the row").not.toHaveBeenCalled();
  });

  it("skips a disabled item", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Menu label="Actions" items={[{ label: "Rename", onSelect, disabled: true }, { label: "Delete", onSelect }]} />);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" })));
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDisabled();
  });
});
```

`frontend/src/components/ui/__tests__/Switch.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Label } from "../Label";
import { Switch } from "../Switch";

describe("Switch", () => {
  it("is a switch that reports the flipped value", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch checked={false} onCheckedChange={onCheckedChange} label="Review pull requests" />);
    const sw = screen.getByRole("switch", { name: "Review pull requests" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sw).toHaveClass("bg-white/20");
    await user.click(sw);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it("shows the on state and does not flip while disabled", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch checked onCheckedChange={onCheckedChange} label="Review pull requests" disabled />);
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(sw).toHaveClass("bg-primary");
    expect(sw).toBeDisabled();
    await user.click(sw);
    expect(onCheckedChange).not.toHaveBeenCalled();
  });

  it("takes its name from a label pointed at its id", () => {
    render(
      <>
        <Label htmlFor="show-advanced">Advanced options</Label>
        <Switch id="show-advanced" checked={false} onCheckedChange={vi.fn()} />
      </>,
    );
    expect(screen.getByLabelText("Advanced options")).toHaveAttribute("role", "switch");
  });
});
```

`frontend/src/components/ui/__tests__/Meter.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Meter, levelOf } from "../Meter";

function meter(value: number, max: number) {
  const { container } = render(<Meter value={value} max={max} label="Budget" />);
  const el = container.querySelector<HTMLElement>("[role='meter']")!;
  const bar = el.firstElementChild as HTMLElement;
  return { el, bar };
}

describe("Meter", () => {
  it("is a meter carrying its bounds and value", () => {
    const { el } = meter(2.5, 10);
    expect(el.getAttribute("aria-label")).toBe("Budget");
    expect(el.getAttribute("aria-valuemin")).toBe("0");
    expect(el.getAttribute("aria-valuemax")).toBe("10");
    expect(el.getAttribute("aria-valuenow")).toBe("2.5");
    expect(el.dataset.level).toBe("ok");
  });

  it("turns amber at 80 percent and red at 100", () => {
    expect(levelOf(7.99, 10)).toBe("ok");
    expect(levelOf(8, 10)).toBe("warn");
    expect(levelOf(10, 10)).toBe("over");
    expect(meter(8, 10).bar).toHaveClass("bg-palette-amber");
    expect(meter(10, 10).bar).toHaveClass("bg-destructive");
    expect(meter(1, 10).bar).toHaveClass("bg-palette-emerald");
  });

  it("caps the bar at full and never divides by zero", () => {
    const over = meter(12.5, 10);
    expect(over.el.dataset.level).toBe("over");
    expect(over.bar.style.width, "over budget still draws a full bar, not a wider one").toBe("100%");
    const empty = meter(3, 0);
    expect(empty.bar.style.width).toBe("0%");
    expect(empty.el.dataset.level).toBe("ok");
    expect(empty.el.getAttribute("aria-valuemax")).toBe("0");
  });
});
```

`frontend/src/components/ui/__tests__/Kbd.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Kbd } from "../Kbd";

describe("Kbd", () => {
  it("renders a kbd element in the mono face", () => {
    render(<Kbd>Esc</Kbd>);
    const kbd = screen.getByText("Esc");
    expect(kbd.tagName).toBe("KBD");
    expect(kbd).toHaveClass("font-mono");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/components/ui/__tests__/Dialog.test.tsx src/components/ui/__tests__/Menu.test.tsx src/components/ui/__tests__/Switch.test.tsx src/components/ui/__tests__/Meter.test.tsx src/components/ui/__tests__/Kbd.test.tsx`
Expected: every file FAILS to load ("Failed to resolve import" for the missing modules). Record the summary line.

- [ ] **Step 3: Write the primitives**

`frontend/src/components/ui/Kbd.tsx`:

```tsx
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** A key on the keyboard, as a hint next to the control it drives. */
export function Kbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
```

`frontend/src/components/ui/Sprite.tsx`:

```tsx
// The roster's sprite, reachable from the primitives folder (spec 3.9). The
// implementation stays with the roster, next to its frames and its tests.
export { Sprite, type SpriteProps } from "@/components/roster";
```

`frontend/src/components/ui/Dialog.tsx`:

```tsx
import { useEffect, useId, useRef, type CSSProperties, type ReactNode } from "react";
import { X } from "lucide-react";
import { Overlay } from "@/components/ui/Overlay";
import { Kbd } from "@/components/ui/Kbd";
import { createFocusTrap } from "@/lib/focus-trap";
import { cn } from "@/lib/utils";

export type DialogSide = "center" | "right" | "left";

export interface DialogProps {
  /** A caller that already decides when to render may omit it; `false` renders nothing. */
  open?: boolean;
  onClose: () => void;
  /** The dialog's accessible name. A `label` outranks a `title`; `labelledBy` points at a heading the caller draws. One of the three is required. */
  label?: string;
  labelledBy?: string;
  /** Drawn as the header: an h2 that names the dialog, the Esc hint and the Close button. Omit it to draw your own header (and your own Close). */
  title?: ReactNode;
  /** A card in the middle (default), or a full-height drawer at the right or left edge. */
  side?: DialogSide;
  /** A click on the backdrop closes (default true). A dialog that must be answered passes false. */
  dismissOnBackdrop?: boolean;
  /** Classes for the panel: its width above all. */
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}

const LAYER: Record<DialogSide, string> = {
  center: "flex items-center justify-center",
  right: "flex justify-end p-0",
  left: "flex justify-start p-0",
};

const PANEL: Record<DialogSide, string> = {
  center: "dialog-in w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-lg border border-border",
  right: "dialog-in-right h-full w-full max-w-lg overflow-y-auto border-l border-border",
  left: "dialog-in-left h-full w-full max-w-lg overflow-y-auto border-r border-border",
};

/**
 * The one modal shell (spec 3.9): an Overlay (the portal, the only box over
 * the whole window), a panel with role="dialog", Escape and the backdrop to
 * close, focus moved in and trapped (lib/focus-trap, which also gives focus
 * back to the opener), and the body's scroll held while it is open. Its
 * entrances are the dialog-in* keyframes in index.css; under reduced motion
 * it is simply there.
 *
 * A control inside that owns Escape (a menu, a field being renamed) calls
 * e.stopPropagation() on its keydown, so the dialog's document listener
 * never sees it.
 */
export function Dialog({
  open = true,
  onClose,
  label,
  labelledBy,
  title,
  side = "center",
  dismissOnBackdrop = true,
  className,
  style,
  children,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !panelRef.current) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const release = createFocusTrap(panelRef.current);
    return () => {
      release();
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;
  const named = label === undefined && title !== undefined ? titleId : undefined;
  return (
    <Overlay className={cn(LAYER[side], "bg-black/60 backdrop-blur-sm")} onClick={dismissOnBackdrop ? onClose : undefined}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-labelledby={labelledBy ?? named}
        className={cn("bg-surface shadow-2xl", PANEL[side], className)}
        style={style}
        onClick={(e) => e.stopPropagation()}
      >
        {title !== undefined && (
          <div className="flex items-center gap-3 border-b border-border px-5 py-4">
            <h2 id={titleId} className="min-w-0 flex-1 text-base font-semibold">
              {title}
            </h2>
            <Kbd className="hidden sm:inline-flex">Esc</Kbd>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {children}
      </div>
    </Overlay>
  );
}
```

`frontend/src/components/ui/Menu.tsx`:

```tsx
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { MoreVertical } from "lucide-react";
import { cn } from "@/lib/utils";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  icon?: ReactNode;
  destructive?: boolean;
  disabled?: boolean;
}

export interface MenuProps {
  /** Names the button and the list for assistive tech, e.g. "Actions for quick-fix". */
  label: string;
  items: MenuItem[];
  /** The edge of the button the list hangs from. */
  align?: "start" | "end";
  /** The button's content; a "more" glyph by default. */
  children?: ReactNode;
  className?: string;
  triggerClassName?: string;
}

/**
 * A button-anchored popover list (spec 3.9): role="menu", arrow keys with
 * wrap, Home and End, Escape back to the button, Tab or a press outside to
 * close. The list is positioned under its button, inside the row that holds
 * it, so a scrolling list clips it like any other row content. Every click
 * inside stops at the menu: a row that is itself a button never fires.
 */
export function Menu({ label, items, align = "end", children, className, triggerClassName }: MenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const itemsOf = () =>
    Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []);

  useEffect(() => {
    if (!open) return;
    const onPress = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPress);
    return () => document.removeEventListener("pointerdown", onPress);
  }, [open]);

  useEffect(() => {
    if (open) itemsOf()[0]?.focus();
  }, [open]);

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }

  function onListKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const all = itemsOf();
    const i = all.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      all[(i + 1) % all.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      all[(i - 1 + all.length) % all.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      all[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      all[all.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.stopPropagation(); // a Dialog around the menu stays open
      close(true);
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  return (
    <div ref={rootRef} className={cn("relative inline-block", className)} onClick={(e) => e.stopPropagation()}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((o) => !o)}
        className={cn("rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground", triggerClassName)}
      >
        {children ?? <MoreVertical className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div
          ref={listRef}
          id={listId}
          role="menu"
          aria-label={label}
          onKeyDown={onListKeyDown}
          className={cn(
            "absolute z-40 mt-1 min-w-32 rounded-md border border-border bg-overlay py-1 shadow-xl",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => {
                close(true);
                item.onSelect();
              }}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors disabled:opacity-50",
                item.destructive ? "text-destructive hover:bg-destructive/10" : "text-foreground/80 hover:bg-muted hover:text-foreground",
              )}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
```

`frontend/src/components/ui/Switch.tsx`:

```tsx
import { cn } from "@/lib/utils";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  /** The switch's own name; omit it when a <Label htmlFor={id}> names it. */
  label?: string;
  id?: string;
  disabled?: boolean;
  className?: string;
}

/** An on/off control (spec 3.9): a button with role="switch", a thumb that slides. */
export function Switch({ checked, onCheckedChange, label, id, disabled, className }: SwitchProps) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-white/20",
        className,
      )}
    >
      <span
        className={cn("inline-block h-4 w-4 rounded-full bg-white transition-transform", checked ? "translate-x-4" : "translate-x-0.5")}
      />
    </button>
  );
}
```

`frontend/src/components/ui/Meter.tsx`:

```tsx
import { cn } from "@/lib/utils";

export type MeterLevel = "ok" | "warn" | "over";

/** Green below 80 percent of max, amber from 80, red at and past 100. A max of zero or less is "ok": there is nothing to measure against. */
export function levelOf(value: number, max: number): MeterLevel {
  if (max <= 0) return "ok";
  const ratio = value / max;
  if (ratio >= 1) return "over";
  if (ratio >= 0.8) return "warn";
  return "ok";
}

const LEVEL_CLASS: Record<MeterLevel, string> = {
  ok: "bg-palette-emerald",
  warn: "bg-palette-amber",
  over: "bg-destructive",
};

export interface MeterProps {
  value: number;
  max: number;
  label: string;
  className?: string;
}

/** A filled bar against a bound (spec 3.9): the budget meter, a progress bar. The bar caps at full; the aria values tell the truth. */
export function Meter({ value, max, label, className }: MeterProps) {
  const level = levelOf(value, max);
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      data-level={level}
      className={cn("h-2 overflow-hidden rounded-full bg-muted", className)}
    >
      <div className={cn("h-full", LEVEL_CLASS[level])} style={{ width: `${pct}%` }} />
    </div>
  );
}
```

`frontend/src/index.css`, appended after the `.pipeline-popup-in` rule's closing `}` (about line 402), before the `/* The animated edge` comment:

```css
/* ----- motion: dialogs ----- */
/* A dialog opens rising in from a little smaller and lower; a drawer slides
   in from its edge (components/ui/Dialog). The keyframes live here, not in
   a component's <style>, so every dialog has its entrance. No forwards fill:
   a transform kept after the entrance would make the panel the containing
   block of every position: fixed box inside it. */
@keyframes dialog-in {
  from { opacity: 0; transform: scale(0.95) translateY(8px); }
}
@keyframes dialog-in-right {
  from { opacity: 0; transform: translateX(16px); }
}
@keyframes dialog-in-left {
  from { opacity: 0; transform: translateX(-16px); }
}

@media (prefers-reduced-motion: no-preference) {
  .dialog-in {
    animation: dialog-in var(--motion-base) var(--ease-standard);
  }
  .dialog-in-right {
    animation: dialog-in-right var(--motion-base) var(--ease-standard);
  }
  .dialog-in-left {
    animation: dialog-in-left var(--motion-base) var(--ease-standard);
  }
}
```

Leave the `pipeline-popup-in` keyframes and rule in place: Task 3 removes them with the popups that use them.

- [ ] **Step 4: Run the tests to verify they pass, then the gate**

Run: the five test files as in Step 2. Expected: all PASS. Then the frontend gate (`npm run build`, `npm run lint`, `npx vitest run`): `Overlay.test.tsx`'s source scan must still pass (Dialog has no `fixed inset-0` literal of its own) and `focus-trap.test.tsx`'s `autoFocus` scan too.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui frontend/src/index.css
git commit -m "feat(ui): Dialog, Menu, Switch, Meter and Kbd primitives"
```

---

### Task 2: Merge decisions from the dashboard (spec 3.8)

**Files:**
- Modify: `embry0/services/merge_approvals.py` (`decide`, line 550; a module constant near `APPROVE_WORDS`, line 204)
- Modify: `embry0/api/v1/merge_approvals.py` (`DecideBody`, line 31; `decide_merge_approval`, line 72)
- Modify: `tests/unit/services/test_merge_approvals.py` (append two tests after `test_no_configured_chat_refuses_every_decision`, line 304)
- Modify: `tests/unit/api/v1/test_merge_approvals_api.py` (one assertion in `test_decide_relays_an_approval`; append two tests)
- Create: `tests/unit/api/v1/test_merge_decide_dashboard_gate.py`
- Modify: `docs/merge-approvals.md` (the route list, lines 93 to 98, and a short section before "Rolling out")

**Interfaces:**
- Consumes: `ensure_admin`, `request_actor` (`embry0/api/deps.py:391`, `:328`), `emit_audit`, `enforce_repo_allowlist`, `restricted_key_name`; the repository's `claim_approval(approval_id, *, decided_by, chat_id, dry_run)` and `reject(approval_id, *, decided_by, chat_id)` (`embry0/storage/repositories/merge_approvals.py:151`, `:178`), whose `chat_id` column is plain text.
- Produces, for plan 3b's merge-approvals page: `POST /api/v1/merge-approvals/{id}/decide` with body `{"decision": "approve" | "reject"}` from an admin session or key → the same result dict as the relay's form (`approval_id`, `outcome`, `status`, `dry_run`, `detail`, `repo`, `pr_number`, `pr_url`, `head_sha`, `merge_sha`, `decided_by`); 403 for anyone below admin; 422 for half a relay body. `MergeApprovalService.decide(..., via: Literal["chat", "dashboard"] = "chat")`. `DASHBOARD_CHANNEL = "dashboard"`. The `merge.decided` audit row gains `"via"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/services/test_merge_approvals.py`, after `test_no_configured_chat_refuses_every_decision`:

```python
@pg
async def test_a_dashboard_decision_needs_no_approval_chat(db_with_migrations, github, notifier) -> None:
    """Spec 3.8: the dashboard is a second channel. With no chat configured at all,
    an admin's decision from the dashboard is taken, recorded under the literal
    channel name, and attributed to the actor the route named."""
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier, merge_approval_chat_id="")
    row = await _ask(svc)
    result = await svc.decide(row["id"], decision="approve", decided_by="ops@example.test", chat_id="", via="dashboard")
    assert result["outcome"] == "would_merge", result
    stored = await repo.get(row["id"])
    assert stored is not None
    assert (stored["decided_by"], stored["chat_id"]) == ("ops@example.test", DASHBOARD_CHANNEL), (
        "a dashboard decision names its actor and the dashboard channel, whatever chat_id was passed"
    )


@pg
async def test_a_dashboard_reject_is_recorded_from_the_dashboard(db_with_migrations, github, notifier) -> None:
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier, merge_approval_chat_id="")
    row = await _ask(svc)
    result = await svc.decide(row["id"], decision="reject", decided_by="ops@example.test", chat_id="", via="dashboard")
    assert result["outcome"] == "rejected", result
    stored = await repo.get(row["id"])
    assert stored is not None and stored["chat_id"] == DASHBOARD_CHANNEL
    assert github.merge_calls == []
```

Add `DASHBOARD_CHANNEL` to the existing `from embry0.services.merge_approvals import (...)` at the top of that file.

In `tests/unit/api/v1/test_merge_approvals_api.py`, extend `test_decide_relays_an_approval` so the audit assertion reads the row's details:

```python
    audit = await api_client.app.state.db.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'merge.decided' ORDER BY id DESC LIMIT 1"
    )
    assert audit is not None, "the decision left no audit row"
    details = audit["details"] if isinstance(audit["details"], dict) else json.loads(audit["details"])
    assert details["via"] == "chat", "the relay's form is the chat channel"
```

(add `import json` to that file), and append:

```python
async def test_the_dashboard_form_decides_with_no_chat_configured(api_client) -> None:
    """Spec 3.8: body {decision} alone is the dashboard's form. The route names
    the actor itself and stores the literal channel. (api_client runs in
    AUTH_DEV_MODE, where the admin gate admits and the actor is the anonymous
    one; the tier itself is pinned in test_merge_decide_dashboard_gate.py.)"""
    _, github, row = await _wire(api_client, merge_approval_chat_id="")
    resp = await api_client.post(f"/api/v1/merge-approvals/{row['id']}/decide", json={"decision": "approve"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "would_merge"
    assert github.merge_calls == []
    stored = await api_client.app.state.merge_approvals.get(row["id"])
    assert stored["chat_id"] == "dashboard"
    assert stored["decided_by"] == body["decided_by"] != ""
    audit = await api_client.app.state.db.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'merge.decided' ORDER BY id DESC LIMIT 1"
    )
    details = audit["details"] if isinstance(audit["details"], dict) else json.loads(audit["details"])
    assert (details["via"], details["decided_by"]) == ("dashboard", body["decided_by"])


async def test_half_a_relay_body_is_422(api_client) -> None:
    _, _, row = await _wire(api_client)
    resp = await api_client.post(
        f"/api/v1/merge-approvals/{row['id']}/decide", json={"decision": "approve", "decided_by": "Alex"}
    )
    assert resp.status_code == 422, resp.text
    assert "together" in resp.json()["detail"]
    assert (await api_client.app.state.merge_approvals.get(row["id"]))["status"] == "pending"
```

Create `tests/unit/api/v1/test_merge_decide_dashboard_gate.py` (the tier, with real bearer auth and a mocked service; the pattern of `tests/unit/api/v1/test_job_session_route.py`'s `tiered_app`):

```python
"""The dashboard form of POST /merge-approvals/{id}/decide is admin-tier (spec 3.8).

The relay's form (decision, decided_by, chat_id) keeps working for the merge
key the chat client holds; the dashboard's form (decision alone) is for an
admin, and the route names the actor from who authenticated.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

pytestmark = pytest.mark.asyncio

KEYS = {"emb_read": "read", "emb_merge": "merge", "emb_operate": "operate", "emb_admin": "admin"}
RESULT = {
    "approval_id": "mrg-1",
    "outcome": "would_merge",
    "status": "approved",
    "dry_run": True,
    "detail": None,
    "repo": "acme/widgets",
    "pr_number": 42,
    "pr_url": "https://github.com/acme/widgets/pull/42",
    "head_sha": "a" * 40,
    "merge_sha": None,
    "decided_by": "x",
}


def _key_row(token: str, scope: str) -> ApiKeyRow:
    return ApiKeyRow(
        id=f"key-{scope}-000000",
        user_id=None,
        name=f"{scope}-key",
        prefix=token[:12],
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")
    rows = {hash_token(token): _key_row(token, scope) for token, scope in KEYS.items()}
    app.state.api_keys_repo = MagicMock()
    app.state.api_keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: rows.get(h))
    app.state.api_keys_repo.touch_last_used = AsyncMock()
    app.state.users_repo = MagicMock()
    service = MagicMock()
    service.mode = "dry_run"
    service.get = AsyncMock(return_value={"id": "mrg-1", "repo": "acme/widgets", "status": "pending"})
    service.decide = AsyncMock(side_effect=lambda *_a, **kw: {**RESULT, "decided_by": kw["decided_by"]})
    app.state.merge_approvals = service
    app.state.db = None  # emit_audit without a database only logs
    bust_api_key_cache()
    yield app
    bust_api_key_cache()


async def _decide(app, token: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/merge-approvals/mrg-1/decide",
            json=body,
            headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
        )


@pytest.mark.parametrize("token", ["emb_read", "emb_merge", "emb_operate"])
async def test_a_key_below_admin_cannot_decide_from_the_dashboard(app, token):
    resp = await _decide(app, token, {"decision": "approve"})
    assert resp.status_code == 403, f"a {KEYS[token]} key decided from the dashboard: {resp.status_code} {resp.text}"
    assert "admin" in resp.json()["detail"]
    app.state.merge_approvals.decide.assert_not_awaited()


@pytest.mark.parametrize("token, actor", [("emb_admin", "key:admin-key"), ("master-key", None)])
async def test_an_admin_key_decides_as_itself(app, token, actor):
    resp = await _decide(app, token, {"decision": "approve"})
    assert resp.status_code == 200, resp.text
    kwargs = app.state.merge_approvals.decide.await_args.kwargs
    assert kwargs["via"] == "dashboard"
    assert kwargs["chat_id"] == "dashboard"
    if actor is not None:
        assert kwargs["decided_by"] == actor, "the actor is who authenticated, not a name the client sent"
    assert kwargs["decided_by"], "the master key still names an actor"
    assert resp.json()["decided_by"] == kwargs["decided_by"]


async def test_the_relay_form_still_works_for_the_merge_key(app):
    resp = await _decide(app, "emb_merge", {"decision": "approve", "decided_by": "Alex", "chat_id": "19:chat@thread.v2"})
    assert resp.status_code == 200, resp.text
    kwargs = app.state.merge_approvals.decide.await_args.kwargs
    assert (kwargs["via"], kwargs["decided_by"], kwargs["chat_id"]) == ("chat", "Alex", "19:chat@thread.v2")


async def test_no_credentials_is_401(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/merge-approvals/mrg-1/decide", json={"decision": "approve"})
    assert resp.status_code == 401, resp.text
```

Check `emit_audit`'s behaviour with `db=None` in `embry0/audit/helpers.py` before relying on the `app.state.db = None` line; if it needs a database, give `app.state.db` a `MagicMock()` with `execute = AsyncMock()` instead and keep the test's assertions as they are.

- [ ] **Step 2: Run the tests to verify they fail**

Run (with `TEST_DATABASE_URL` set, see Global Constraints): `python -m pytest tests/unit/services/test_merge_approvals.py -k dashboard tests/unit/api/v1/test_merge_approvals_api.py tests/unit/api/v1/test_merge_decide_dashboard_gate.py -q`
Expected: the service file fails at import (`DASHBOARD_CHANNEL`), the API tests fail (`decide()` gets an unexpected keyword `via`; the dashboard form is 422 on the old body model; the gate file's admin tests get 422). Record the summary line.

- [ ] **Step 3: Change the service**

In `embry0/services/merge_approvals.py`, next to `APPROVE_WORDS` (line 204):

```python
#: The chat_id stored for a decision made on the dashboard (spec 3.8): the
#: column says which channel answered, and this one has no chat.
DASHBOARD_CHANNEL = "dashboard"
```

Replace the head of `decide` (lines 550 to 559) with:

```python
    async def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str,
        chat_id: str,
        via: Literal["chat", "dashboard"] = "chat",
    ) -> dict[str, Any]:
        """Take a human's decision. ``via="chat"`` is the relay's: the answer must
        come from the configured approval chat. ``via="dashboard"`` is an admin
        on the dashboard (the route has already applied the admin gate and named
        ``decided_by`` from who authenticated): no chat is involved, and the
        stored ``chat_id`` is :data:`DASHBOARD_CHANNEL`. Everything after the
        channel check is the same for both."""
        word = (decision or "").strip().lower()
        if word not in APPROVE_WORDS | REJECT_WORDS:
            raise DecisionRefused(422, f"decision must be 'approve' or 'reject', not {decision!r}")
        if not (decided_by or "").strip():
            raise DecisionRefused(422, "decided_by is required: name the person who answered")
        if via == "dashboard":
            chat_id = DASHBOARD_CHANNEL
        else:
            if not self.chat_id:
                raise DecisionRefused(403, "no approval chat is configured (MERGE_APPROVAL_CHAT_ID); nothing can be merged")
            if (chat_id or "").strip() != self.chat_id:
                raise DecisionRefused(403, "merge decisions are accepted only from the embry0 approval chat")
```

The rest of the method (the row lookup, `handles`, reject, claim, `_approve`) is unchanged.

- [ ] **Step 4: Change the route**

In `embry0/api/v1/merge_approvals.py`, replace `DecideBody` with:

```python
class DecideBody(BaseModel):
    """Two forms on one route. The relay's carries ``decided_by`` and ``chat_id``
    (who answered, in which chat); the dashboard's carries ``decision`` alone,
    and the route names the actor from who authenticated (spec 3.8)."""

    decision: str = Field(min_length=1, max_length=20, description="'approve' or 'reject'")
    decided_by: str | None = Field(
        default=None, min_length=1, max_length=200, description="who answered in the chat (the relay's form)"
    )
    chat_id: str | None = Field(
        default=None, min_length=1, max_length=300, description="the chat the answer came from (the relay's form)"
    )
```

Change the imports: `from embry0.api.deps import ensure_admin, request_actor` and `from embry0.services.merge_approvals import DASHBOARD_CHANNEL, DecisionRefused, MergeApprovalService`.

Replace the body of `decide_merge_approval` from its first line through the `service.decide(...)` call with:

```python
    service = _service(request)
    if (body.decided_by is None) != (body.chat_id is None):
        raise HTTPException(
            status_code=422,
            detail="decided_by and chat_id come together: both for the relay's form, neither for the dashboard's",
        )
    if body.decided_by is None or body.chat_id is None:
        # The dashboard's form: an admin, named by the route, with no chat in the loop.
        await ensure_admin(request)
        via, decided_by, chat_id = "dashboard", request_actor(request), DASHBOARD_CHANNEL
    else:
        via, decided_by, chat_id = "chat", body.decided_by, body.chat_id
    if restricted_key_name(request) is not None:
        # Only a restricted key pays for the extra read; an unknown id falls
        # through to decide(), which owns the 404.
        row = await service.get(approval_id)
        if row is not None:
            await enforce_repo_allowlist(request, row.get("repo"), action="decide a merge")
    try:
        result = await service.decide(approval_id, decision=body.decision, decided_by=decided_by, chat_id=chat_id, via=via)
    except DecisionRefused as refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.detail) from None
```

and in the `emit_audit` details replace `"decided_by": body.decided_by,` with `"decided_by": decided_by, "via": via,`. Add `via: Literal["chat", "dashboard"]` typing with `from typing import Any, Literal, cast` so mypy accepts the tuple assignment (annotate: `via: Literal["chat", "dashboard"]`, `decided_by: str`, `chat_id: str` before the branch).

- [ ] **Step 5: Document**

`docs/merge-approvals.md`: in the route list (line 95) replace the `POST` bullet with:

```markdown
- `POST /api/v1/merge-approvals/{id}/decide`, two forms on one route →
  `{outcome: merged|would_merge|rejected|refused|stale|failed|already_decided, status, detail, …}`:
  - the relay's: `{decision: approve|reject, decided_by, chat_id}`, with `chat_id` checked against `MERGE_APPROVAL_CHAT_ID`;
  - the dashboard's: `{decision}` alone, admin tier (an admin session or an admin-scope key); the actor is who authenticated, the stored `chat_id` is the literal `dashboard`, and no chat needs to be configured.
  A body with one of `decided_by` and `chat_id` but not the other is 422.
  Each decision writes a `merge.decided` audit row carrying `via` (`chat` or `dashboard`).
```

and add, before "## Rolling out":

```markdown
## Deciding from the dashboard

The approval chat is one channel; the dashboard's merge-approvals page is the
other (spec 3.8). An admin presses approve or reject there, the dashboard
sends `{decision}` and nothing else, and embry0 names the actor from the
session or key that made the call. The same re-checks run (PR head unchanged,
CI green, verification gate not blocking, mergeable); `MERGE_APPROVAL_REPOS`
still decides which repositories are asked about at all. A `merge`-scope key
cannot use this form: it is the relay's key, and the relay answers for a chat.
```

Run the docs guards in the gate (`tests/unit/docs`) and fix any table or link they flag.

- [ ] **Step 6: Run the tests to verify they pass, then the gate**

Run the Task 2 backend gate from the Global Constraints. Expected: all PASS (the `requires_postgres` ones run, not skip: read the summary line and confirm the skip count is 0 for the merge files); `ruff check`, `ruff format --check` and `mypy embry0` clean.

- [ ] **Step 7: Commit**

```bash
git add embry0/services/merge_approvals.py embry0/api/v1/merge_approvals.py tests/unit/services/test_merge_approvals.py tests/unit/api/v1/test_merge_approvals_api.py tests/unit/api/v1/test_merge_decide_dashboard_gate.py docs/merge-approvals.md
git commit -m "feat(api): accept a merge decision from an admin on the dashboard"
```

---

### Task 3: The pipeline editor's overlays on Dialog and Menu (issue O, second half)

**Files:**
- Modify: `frontend/src/components/pipeline-editor/AgentDetailPopup.tsx` (the effects at lines 219-235, the render shell at lines 300-315 and its closing tags)
- Modify: `frontend/src/components/pipeline-editor/EdgeDetailPopup.tsx` (effects at lines 64-76, shell at lines 82-104 and its closing tags)
- Modify: `frontend/src/components/pipeline-editor/TemplatePicker.tsx` (whole shell)
- Modify: `frontend/src/components/pipeline-editor/TemplateDrawer.tsx` (whole shell, the row menu, the rename field)
- Rewrite: `frontend/src/components/pipeline-editor/__tests__/popupEntrance.test.tsx`
- Create: `frontend/src/components/pipeline-editor/__tests__/TemplateDrawer.test.tsx`
- Modify: `frontend/src/index.css` (delete the `/* ----- motion: pipeline editor ----- */` comment, the `@keyframes pipeline-popup-in` block and the `.pipeline-popup-in` media rule, lines 388-402; keep the "animated edge" rules that follow)
- Modify: `docs/glossary.md` (the `### TemplateDrawer` entry, line 183)

**Interfaces:**
- Consumes: `Dialog` and `Menu` from Task 1 as they exist on the branch (`@/components/ui/Dialog`, `@/components/ui/Menu`).
- Produces: nothing new; the four components keep their props (`node | edge | onSelect`, `onUpdate`, `onClose`). `PipelineEditor.tsx` does not change: it still mounts them conditionally in the same places.

Tests that must keep passing unchanged, and why they will: `detailDialogs.test.tsx` (the agent dialog is named by its title through `labelledBy`; the edge dialog by `label`; Escape still closes, focus still returns through `createFocusTrap`; Tab stays inside), `keyboardDetail.test.tsx` (same), `lib/__tests__/focus-trap.test.tsx` (the `DIALOGS` table), `toolbarCase.test.tsx` (the picker's heading is Dialog's h2 "Load pipeline template"), `fieldLabels.test.tsx`, `AgentDetailPopup.skills.test.tsx`.

- [ ] **Step 1: Write the failing tests**

Rewrite `frontend/src/components/pipeline-editor/__tests__/popupEntrance.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Edge, Node } from "@xyflow/react";
import css from "../../../index.css?raw";

vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useSkills", () => ({ useSkills: () => ({ data: [], isLoading: false }) }));

import { AgentDetailPopup } from "../AgentDetailPopup";
import { EdgeDetailPopup } from "../EdgeDetailPopup";

/**
 * The editor's detail popups are Dialogs (spec 3.9): they rise in with the
 * dialog entrance that index.css owns, and the editor's own entrance of old
 * is gone with the hand-rolled shells that used it.
 */

const node = { id: "n1", position: { x: 0, y: 0 }, data: { agentType: "developer" } } as Node;
const edge = { id: "e1", source: "triage", target: "developer", data: {} } as Edge;

function inlineAnimations(): string[] {
  return [...document.body.querySelectorAll<HTMLElement>("*")]
    .map((el) => el.style.animation || el.style.animationName)
    .filter(Boolean);
}

describe("pipeline editor popup entrance", () => {
  it("gives the agent popup the dialog entrance, over the window", () => {
    render(<AgentDetailPopup node={node} onUpdate={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Developer" });
    expect(dialog).toHaveClass("dialog-in");
    expect(dialog.parentElement?.parentElement, "a Dialog, so a child of body, not of the canvas").toBe(document.body);
    expect(inlineAnimations()).toEqual([]);
  });

  it("gives the edge popup the dialog entrance, with no stylesheet of its own", () => {
    const { container } = render(<EdgeDetailPopup edge={edge} onUpdate={() => {}} onClose={() => {}} />);
    expect(container.querySelectorAll("style")).toHaveLength(0);
    const dialog = screen.getByRole("dialog", { name: "Flow edge from triage to developer" });
    expect(dialog).toHaveClass("dialog-in");
    expect(inlineAnimations()).toEqual([]);
  });

  it("left no entrance of its own behind in index.css", () => {
    expect(css, "the pipeline-popup-in keyframes went with the hand-rolled shells").not.toContain("pipeline-popup-in");
  });
});
```

Create `frontend/src/components/pipeline-editor/__tests__/TemplateDrawer.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const TEMPLATES = [
  { template_id: "t1", name: "quick-fix", description: "One developer, one reviewer", created_at: "2026-10-01T00:00:00Z" },
  { template_id: "t2", name: "research", description: "", created_at: "2026-10-02T00:00:00Z" },
];
let askedFor: string | null = null;
const rename = vi.fn();
const remove = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => ({ data: TEMPLATES, isLoading: false }),
  useTemplate: (id: string | null) => {
    askedFor = id;
    return { data: undefined };
  },
  useRenameTemplate: () => ({ mutate: rename, isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteTemplate: () => ({ mutate: remove, isPending: false }),
}));
vi.mock("@/api/pipelines", () => ({ fetchTemplate: vi.fn() }));

import { TemplateDrawer } from "../TemplateDrawer";

beforeEach(() => {
  askedFor = null;
  rename.mockReset();
  remove.mockReset();
});

describe("TemplateDrawer", () => {
  it("is a drawer dialog named Templates at the left edge", () => {
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Templates" });
    expect(dialog).toHaveClass("dialog-in-left");
    expect(dialog).not.toHaveClass("animate-in");
    expect(screen.getByText("quick-fix")).toBeInTheDocument();
  });

  it("loads a template from its card", () => {
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("quick-fix"));
    expect(askedFor, "the card asks for the full template").toBe("t1");
  });

  it("opens a row's menu without loading the template", async () => {
    const user = userEvent.setup();
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Rename", "Duplicate", "Delete"]);
    expect(askedFor, "the menu click never reached the card").toBeNull();
  });

  it("Escape in the rename field leaves rename mode and keeps the drawer open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TemplateDrawer onSelect={vi.fn()} onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByRole("textbox", { name: "Template name" });
    await waitFor(() => expect(document.activeElement).toBe(field));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "Template name" })).toBeNull();
    expect(onClose, "the field owned that Escape").not.toHaveBeenCalled();
    expect(rename).not.toHaveBeenCalled();
  });

  it("renames on Enter", async () => {
    const user = userEvent.setup();
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByRole("textbox", { name: "Template name" });
    await user.clear(field);
    await user.type(field, "quick-fix-2{Enter}");
    expect(rename).toHaveBeenCalledWith({ templateId: "t1", name: "quick-fix-2" }, expect.anything());
  });

  it("deletes after asking", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for research" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(confirm).toHaveBeenCalled();
    expect(remove).toHaveBeenCalledWith("t2", expect.anything());
    confirm.mockRestore();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/components/pipeline-editor/__tests__/popupEntrance.test.tsx src/components/pipeline-editor/__tests__/TemplateDrawer.test.tsx`
Expected: FAIL. The entrance test: the agent dialog has class `pipeline-popup-in`, not `dialog-in`, and sits inside the render container; `css` still contains `pipeline-popup-in`. The drawer test: no element with role dialog ("Unable to find an accessible element with the role dialog"). Record the summary lines.

- [ ] **Step 3: Move the two detail popups onto Dialog**

`AgentDetailPopup.tsx`:
- Imports: drop `createFocusTrap`; add `import { Dialog } from "@/components/ui/Dialog";`. Keep `X`, `useId`, `useState`, `useEffect` only if still used elsewhere in the file (the Escape effect and the trap effect go; `containerRef` goes).
- Delete the two effects at lines 219-235 (Escape listener; scroll lock + trap) and `const containerRef = useRef<HTMLDivElement>(null);`.
- Replace the shell (the outer `<div className="absolute inset-0 z-50 ...">` and the inner `role="dialog"` div, lines 300-315) with:

```tsx
    <Dialog
      onClose={onClose}
      labelledBy={titleId}
      className="w-[560px]"
      style={{ border: `1px solid ${color}33` }}
    >
```

and close it with `</Dialog>` where the two divs closed. The header (avatar, title with `id={titleId}`, the Close button) and the body stay exactly as they are; the panel's `rounded-lg`, `max-h-[85vh]` and `overflow-y-auto` now come from Dialog, and the inline `background` and `boxShadow` go (Dialog's `bg-surface shadow-2xl`).

`EdgeDetailPopup.tsx`:
- Imports: drop `createFocusTrap`; add `Dialog`. Drop `containerRef` and the two effects at lines 64-76.
- Replace the shell (lines 82-104: the backdrop div and the card div) with:

```tsx
    <Dialog
      onClose={onClose}
      label={`${isFeedback ? "Feedback loop" : "Flow edge"} from ${edge.source} to ${edge.target}`}
      className={cn("w-[400px]", isFeedback ? "border-destructive/20" : "border-white/[0.08]")}
    >
```

and close with `</Dialog>`. The header, body and footer are unchanged.

- [ ] **Step 4: Move the picker onto Dialog**

Replace the body of `TemplatePicker.tsx`'s return with:

```tsx
  return (
    <Dialog onClose={onClose} title="Load pipeline template" className="max-w-[500px]">
      <div className="flex max-h-[400px] flex-col">
        <div className="flex-1 overflow-y-auto p-2">
          {isLoading && <div className="p-4 text-sm text-white/40">Loading...</div>}
          {!isLoading && (!templates || templates.length === 0) && (
            <div className="p-4 text-sm text-white/40">No templates saved yet.</div>
          )}
          {templates?.map((t) => (
            <button
              key={t.template_id}
              type="button"
              onClick={() => setSelectedId(t.template_id)}
              aria-pressed={selectedId === t.template_id}
              className={`mb-1 w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${
                selectedId === t.template_id ? "bg-primary/20 text-white" : "text-white/70 hover:bg-white/5"
              }`}
            >
              <div className="font-medium">{t.name}</div>
              <div className="text-xs text-white/40">{t.description}</div>
            </button>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] p-3">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" size="sm" onClick={handleApply} disabled={!selectedTemplate}>
            Load template
          </Button>
        </div>
      </div>
    </Dialog>
  );
```

Replace the `Overlay` import with `import { Dialog } from "@/components/ui/Dialog";`. The `<h3>` goes: Dialog's `title` is the heading `toolbarCase.test.tsx` looks for.

- [ ] **Step 5: Move the drawer onto Dialog and its row menu onto Menu**

`TemplateDrawer.tsx`:
- Imports: `import { Dialog } from "@/components/ui/Dialog";`, `import { Menu } from "@/components/ui/Menu";`; from lucide keep `Copy, Pencil, Trash2`, drop `X` and `MoreVertical`.
- Delete `menuOpenId`, `menuRef`, the outside-click effect (lines 29-40) and every `setMenuOpenId(...)` call in the handlers.
- Replace the return's outer `<div className="absolute inset-y-0 left-0 z-30 ...">` and its header block with:

```tsx
    <Dialog onClose={onClose} title="Templates" side="left" className="w-[260px] max-w-[85vw]">
      <div className="flex-1 space-y-1 overflow-y-auto p-2">
```

(the list `div` keeps its content; close with `</div>` and `</Dialog>`).

- The rename field gains a name and owns its Escape:

```tsx
              <input
                ref={renameInputRef}
                aria-label="Template name"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={() => handleRenameSubmit(t.template_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRenameSubmit(t.template_id);
                  if (e.key === "Escape") {
                    e.stopPropagation(); // ours, not the drawer's
                    setRenamingId(null);
                  }
                }}
                className="w-full rounded border border-white/20 bg-white/[0.06] px-1.5 py-0.5 text-xs text-white outline-none"
                onClick={(e) => e.stopPropagation()}
              />
```

- Replace the `MoreVertical` button and the whole `{menuOpenId === t.template_id && (...)}` dropdown with one `Menu` beside the name:

```tsx
              <div className="flex items-center justify-between">
                <span className="truncate text-xs font-medium text-white/80">{t.name}</span>
                <Menu
                  label={`Actions for ${t.name}`}
                  triggerClassName="opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 group-focus-within:opacity-100"
                  items={[
                    { label: "Rename", icon: <Pencil size={11} />, onSelect: () => handleRenameStart(t) },
                    { label: "Duplicate", icon: <Copy size={11} />, onSelect: () => void handleDuplicate(t) },
                    { label: "Delete", icon: <Trash2 size={11} />, destructive: true, onSelect: () => handleDelete(t.template_id) },
                  ]}
                />
              </div>
```

`handleDelete` keeps `window.confirm` (a confirm Dialog over a drawer is plan 3b's hosts page's problem to solve first; a ruling in the ledger if the reviewer asks).

- [ ] **Step 6: Remove the old entrance and update the glossary**

`frontend/src/index.css`: delete lines 388-402 (the `/* ----- motion: pipeline editor ----- */` comment through the closing `}` of the `.pipeline-popup-in` media rule). The `/* The animated edge` comment and the `.react-flow .react-flow__edge.animated path` rules stay.

`docs/glossary.md`, the `### TemplateDrawer` entry:

```markdown
### TemplateDrawer
The drawer in the [PipelineEditor](#pipelineeditor) that lists saved [templates](#template): a `Dialog` at the left edge, closed by Escape, its backdrop or its Close button. Cards show the template name and description; click a card to load its `PipelineGraph` into the canvas. Each card's menu renames, duplicates or deletes the template.
```

- [ ] **Step 7: Run the tests to verify they pass, then the gate**

Run: the two files from Step 2, then `npx vitest run src/components/pipeline-editor src/lib/__tests__/focus-trap.test.tsx src/components/ui`. Expected: all PASS, including the unchanged `detailDialogs`, `keyboardDetail`, `toolbarCase` and `focus-trap` tests. Then the full frontend gate.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/pipeline-editor frontend/src/index.css docs/glossary.md
git commit -m "refactor(pipeline-editor): the detail popups, the picker and the drawer on Dialog and Menu"
```

---

### Task 4: The job dialog, the transcript drawer, the switch and the meter

**Files:**
- Modify: `frontend/src/components/jobs/CreateJobDialog.tsx`, `frontend/src/components/jobs/__tests__/CreateJobDialog.test.tsx` (one test appended)
- Modify: `frontend/src/pages/JobsPage.tsx` (the New job button, line 47)
- Modify: `frontend/src/components/logs/TranscriptDrawer.tsx`
- Modify: `frontend/src/components/jobs/JobHud.tsx`
- Modify: `frontend/src/components/environments/PullRequestReviewCard.tsx` (lines 112-131)

**Interfaces:**
- Consumes: `Dialog`, `Switch`, `Meter` and `levelOf` from Task 1.
- Produces: `CreateJobDialog` is a modal (same props); `TranscriptDrawer`, `JobHud` and `PullRequestReviewCard` keep their props and their tests.

Tests that must keep passing unchanged: `logs/__tests__/TranscriptDrawer.test.tsx` (name, Escape on `document`, focus to the Close button on open since it is the first focusable in Dialog's header, focus back to the opener), `jobs/__tests__/JobHud.test.tsx` (`role="meter"`, `data-level`, the aria values, "no budget, no meter"), `environments/__tests__/PullRequestReviewCard.test.tsx` (`getByRole("switch", { name: "Review pull requests" })`, `aria-checked`), `jobs/__tests__/noPulse.test.ts`, and the five existing `CreateJobDialog` tests (`getByLabelText("Advanced options")` now finds the Switch through its `Label`).

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/jobs/__tests__/CreateJobDialog.test.tsx`, inside the `describe`:

```tsx
  it("is a modal dialog named for what it does, and Cancel closes it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CreateJobDialog onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: "Create New Job" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.parentElement?.parentElement, "a Dialog over the page, not a card in it").toBe(document.body);
    expect(screen.getByLabelText("Advanced options")).toHaveAttribute("role", "switch");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run, from `frontend/`: `npx vitest run src/components/jobs/__tests__/CreateJobDialog.test.tsx`
Expected: the new test FAILS ("Unable to find an accessible element with the role dialog"); the five others pass. Record the summary line.

- [ ] **Step 3: The job dialog and its page**

`CreateJobDialog.tsx`:
- Imports: drop `Card, CardContent, CardHeader, CardTitle`; add `import { Dialog } from "@/components/ui/Dialog";` and `import { Switch } from "@/components/ui/Switch";`.
- Replace `<Card><CardHeader><CardTitle className="text-lg">Create New Job</CardTitle></CardHeader><CardContent>` with `<Dialog onClose={onClose} title="Create New Job" className="max-w-2xl"><div className="p-5">` and the closing `</CardContent></Card>` with `</div></Dialog>`.
- Replace the "Advanced options" checkbox block with:

```tsx
          <div className="flex items-center gap-3">
            <Switch id="show-advanced" checked={showAdvanced} onCheckedChange={setShowAdvanced} />
            <Label htmlFor="show-advanced">Advanced options</Label>
          </div>
```

`JobsPage.tsx`: the button becomes `<Button onClick={() => setShowCreate(true)}>` (a modal is opened, not toggled); the `{showCreate && <CreateJobDialog onClose={() => setShowCreate(false)} />}` line stays where it is.

- [ ] **Step 4: The transcript drawer**

`TranscriptDrawer.tsx`: drop the `useEffect`/`useRef` imports, `X`, `Overlay` and `closeRef`; add `import { Dialog } from "@/components/ui/Dialog";`. Delete both effects. Replace the return with:

```tsx
  if (!open) return null;
  const title = `Stored session of ${displayName} for this job`;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      side="right"
      label={title}
      title={
        <>
          <span className="title-pixel text-lg">Stored session</span>
          <span className="ml-2 text-sm font-normal text-muted-foreground">of {displayName} for this job</span>
        </>
      }
      className="max-w-3xl bg-card"
    >
      <div className="p-4">
        {isLoading && <p className="text-sm text-muted-foreground">Loading the stored session…</p>}
        {forbidden && <p className="text-sm text-destructive">Operator access is needed to read stored sessions.</p>}
        {isError && !forbidden && <p className="text-sm text-destructive">The stored session could not be loaded.</p>}
        {!isLoading && !isError && !data && <p className="text-sm text-muted-foreground">{`No stored session of ${displayName} for this job.`}</p>}
        {data?.last_turn_at && <p className="mb-2 text-xs text-muted-foreground">Last turn {new Date(data.last_turn_at).toLocaleString()}</p>}
        {data && <Messages session={data} />}
      </div>
    </Dialog>
  );
```

Update the docstring: the trap, Escape and focus now come from `Dialog`; the `(plan 3's Dialog brings the trap)` remark goes. `Messages` is unchanged.

- [ ] **Step 5: The meter and the switch**

`JobHud.tsx`: delete `Level`, `levelOf` and `LEVEL_CLASS`; `import { Meter } from "@/components/ui/Meter";`; delete the `level` and `pct` lines; replace the `role="meter"` div (and its child) with `<Meter value={costUsd} max={budgetUsd} label="Budget" className="flex-1" />`. `hasBudget` and the surrounding `Budget` and cost spans stay.

`PullRequestReviewCard.tsx`: replace the `<button type="button" role="switch" ...>...</button>` (lines 112-131) with:

```tsx
        <Switch
          checked={settings.enabled}
          onCheckedChange={(enabled) => update({ enabled })}
          label="Review pull requests"
          disabled={locked}
        />
```

and add `import { Switch } from "@/components/ui/Switch";`. Remove `cn` from the imports only if nothing else in the file uses it.

- [ ] **Step 6: Run the tests to verify they pass, then the gate**

Run: `npx vitest run src/components/jobs src/components/logs src/components/environments/__tests__/PullRequestReviewCard.test.tsx src/pages/__tests__/JobDetailPage.test.tsx`. Expected: all PASS with no test changed but the one appended. Then the full frontend gate.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/jobs frontend/src/pages/JobsPage.tsx frontend/src/components/logs/TranscriptDrawer.tsx frontend/src/components/environments/PullRequestReviewCard.tsx
git commit -m "refactor(dashboard): the job dialog, the transcript drawer, the review switch and the budget meter on the primitives"
```

---

## Integration and verification (controller)

After each wave: merge the task branches into `feat/game-ui-3a`, grep the tree for conflict markers, run the full frontend gate and (after wave 1) the Task 2 backend gate on the integrated branch. After wave 2, one whole-branch review on the most capable model, one fix wave, then the pull request with spec, plan and code. Before the PR: a manual pass on a deployment's stack with the branch's frontend beside it (the recipe from plan 2: a verify clone, `docker build frontend/`, a container on the stack's frontend network), opening each of the six dialogs with the keyboard and a pointer at desktop and 390 px widths, and confirming the dashboard decision route answers 403 to a read key and 200 to an admin key on a dry-run ask (or 404 when the deployment has no ask to decide on).
