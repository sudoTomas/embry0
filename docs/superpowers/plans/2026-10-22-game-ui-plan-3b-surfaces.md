# Game UI, plan 3b of 3: the surfaces (issues P and Q) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eight routes the backend already serves get a page or a section on the dashboard, each with a hook, a loading state, a `PageError`, tier gates that match the route's own and render tests: merge approvals (list, one ask, approve or reject), template badges and actions, deep stats under the Overview tiles, a Stacks tab on the Watcher page, the effective environment of a repository, a QA image build button, a Hosts page and the organization's actions and settings.

**Architecture:** Every surface is the same shape: an `api/*.ts` module with the route's exact body and answer, a `hooks/*.ts` module over react-query, a page or a section that reads the hook and draws with the primitives plan 3a landed (`Dialog`, `Menu`, `Switch`, `Kbd`) and the older ones (`Badge`, `Button`, `AdminButton`, `Tabs`, `FilterSelect`, `TagInput`, `EmptyState`, `PageError`, `TableSkeleton`), and tests that mock the hook or the api module at the module boundary. One new primitive, `ConfirmDialog`, carries every "are you sure" of this plan. One backend change: `PUT /pipelines/templates/{id}` learns to clear `default_for_kind` with an explicit null and answers 409 instead of 500 when another template already holds the kind, because the UI cannot move a default otherwise.

**Tech Stack:** React 19, Tailwind v4 tokens from `index.css`, lucide icons, react-query, vitest + Testing Library + user-event, TypeScript 5.9; FastAPI + pydantic, asyncpg, pytest. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-game-ui-design.md`, section 3.7 (the surfaces table), 3.8 (the decision route, built in plan 3a), 3.10, 4, 5 and issues P and Q of section 6. Plan 3a (`docs/superpowers/plans/2026-10-21-game-ui-plan-3a-primitives.md`) built the primitives this plan draws with and the dashboard form of the decision route.

**Deviations from the spec, stated:**

- *Merge approvals.* The spec reads `GET /merge-approvals/{id}` for "one ask"; this plan gives the ask its own page at `/merge-approvals/:approvalId` rather than a dialog, so a decision's confirm dialog never opens inside another dialog, and so a chat message can link an ask.
- *Templates.* The spec names `PUT` with `default_for_kind`; moving a default needs the old holder cleared first, and the route drops a null body field and 500s on the unique index. Task 2 makes an explicit null clear and a clash a 409.
- *QA image build.* The spec wants "a result toast with the tag"; `POST /qa/images/build` answers `{repo, branch, status: built | skipped}` with no tag. The toast says built or already current.
- *Watcher stacks.* The spec marks the tab "(admin)"; the stacks `GET` is operator-tier like the rest of the watcher API, so the tab is readable by every tier and its writes are gated, which is the rule "tier gates match the route's own".
- *Organization actions.* `POST /orgs/{owner}/probe` and the status change sit in the header and a Settings tab; the webhook, display name and `auto_register` live in the Settings tab; the rediscovery schedule stays in the header where its tests pin it.

## Global Constraints

- No new runtime dependency, no image files, no migration, no compose change, no sandbox image rebuild. Deploy is `infra/deploy.sh` (orchestrator for Task 2, frontend for everything else). No new API route: every path here is in `docs/api.md` already, so "What each ingress reaches" needs no edit.
- **Motion policy** (`frontend/src/index.css`, the "Motion" comment block; `frontend/src/components/divine/CLAUDE.md`): nothing in this plan adds a keyframe or a `<style>`; `motion-safe:animate-pulse` is allowed only on a skeleton, never under `components/jobs/` or `components/logs/`. No divine element on a dialog, a menu, a switch or a table.
- `Overlay` stays **the only `fixed inset-0` box**; every overlay here is a `Dialog` (`@/components/ui/Dialog`) or a `Menu` (`@/components/ui/Menu`). **No Dialog opens from inside another Dialog**: a confirm opens from a page, a row or a tab, and the merge decision's confirm sits on the ask's page, not in a dialog. A control that owns Escape inside a Dialog calls `e.stopPropagation()` on its keydown.
- `createFocusTrap` is the only focus trap; a field that should take focus first carries `data-autofocus`, never React's `autoFocus`.
- **Gold is spent.** The Overview's one gold is the Needs-you tile; the organization page's one gold is the act panel's Apply (`OrganizationPage.goldenTouch.test.tsx`). Nothing added to those pages uses `bg-primary`, `bg-primary-dim` or `text-primary`: every new button there is `variant="outline"`, `"ghost"` or `"destructive"`. A new page (merge approvals, hosts) may give its one primary action the default gold.
- **Tier gates match the route's own.** A control for an admin route (`require_admin` or `ensure_admin` on the backend) is an `AdminButton` (`@/components/auth/AdminButton`) or a `Menu` item with `disabled: !isAdmin`, read from `useIsAdmin()` (`@/stores/authStore`). A control for an operator route is a plain `Button`. Reads are never gated on the client.
- **Copy rules:** sentence case everywhere, no raw identifiers shown to a person (`sentenceCase`, `statusLabel` from `@/lib/status`), no ticket ids (`tests/unit/test_user_facing_text.py` scans the backend; the frontend copy follows the same rule), vendor-neutral names (`CONTRIBUTING.md`). Test fixtures use `acme/widgets`-style repositories, `ops@example.test` addresses, `web-1` host names and `100.64.0.10` addresses.
- Every list has a loading state (`TableSkeleton` or `Skeleton`), an error state (`PageError` with `onRetry`) and an empty state (`EmptyState` or a one-line muted sentence); a 503 that means "the feature is off" is an empty state, not an error (the pattern `fetchPendingMergeApprovals` already uses).
- The axios client (`api/client.ts`) toasts 5xx, network errors and 422 (unless `suppressValidationToast: true`); 403 and 409 are the caller's to explain. A mutation's error goes through `toastApiError(e, "<what failed>")` (`@/api/errorToast`) unless the task says it is shown inline.
- Commits follow `.claude/skills/commit-standard` and end with `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`. No AI co-author footers.
- **Frontend gate for every frontend task**, from `frontend/`: `npm run build`, `npm run lint` (0 errors; 4 warnings pre-exist), `npx vitest run` (read the summary line, not a grep).
- **Backend gate for Task 2**, from the worktree root with a venv that has the dev extras and the throwaway Postgres `embry0-test-pg` on `127.0.0.1:55432` (`CLAUDE.md`, "The local test baseline is not green"; give it a fresh password with `docker exec -i embry0-test-pg psql -U embry0 <<<"ALTER USER embry0 PASSWORD '$EMBRY0_TEST_PG_PASSWORD';"` and a private database with `docker exec embry0-test-pg createdb -U embry0 "$DB"`): `TEST_DATABASE_URL=... python -m pytest tests/unit/api/v1/test_pipeline_templates_api.py tests/unit/api/test_route_table.py tests/unit/docs tests/unit/test_user_facing_text.py -q`, then `ruff check` and `ruff format --check` on the touched files and `mypy embry0`.
- Every new test is shown failing on the tree before its code (a clean `git archive` of the base commit, not a stash), with its own assertion message; report the RED and GREEN summary lines.

## Review Focus

Five inputs the spec implies but names no test for; each line's test is in the task that owns the code.

1. **An ask decided in the chat while its page shows it pending**: the decision from the page answers with the chat's outcome (`already decided`), the page refetches and shows who decided, and no error is raised. Pinned in Task 1 (`MergeApprovalPage.test.tsx`, "shows the outcome when the ask was decided elsewhere first").
2. **Moving a default from one template to another**: making B the default for `code` while A holds it answers 409 naming the kind, A keeps it, and the toast carries the server's sentence. Pinned in Task 2 (`test_pipeline_templates_api.py`, "a second default for a kind is 409") and Task 6 (`PipelinesPage.test.tsx`, "shows the server's reason when a default is taken").
3. **A stack's settings JSON that is not an object** (an array, a string, a number): refused before the `PUT`, with the field named. Pinned in Task 8 (`WatcherStacksTab.test.tsx`, "refuses settings that are not an object").
4. **The effective environment of a repository with no variables of its own**: the merged list shows the global ones, each marked Global, and the "no variables" sentence does not appear. Pinned in Task 5 (`RepoDetailPage.test.tsx`, "marks every effective variable Global when the repo sets none").
5. **A host edit the server refuses** (an `ssh` transport with an address that is not `host` or `host:port`): the 422 detail is shown inside the form, which stays open with the values typed. Pinned in Task 7 (`HostFormDialog.test.tsx`, "keeps the form open and shows the server's refusal").

---

## Wave plan and file ownership

Two waves. Wave 1 runs Tasks 1 to 5 in parallel in per-task worktrees cut from `feat/game-ui-3b`; wave 2 runs Tasks 6 to 9 in parallel after wave 1 is integrated. A file is owned by exactly one task per wave; a task that needs a change in another task's file asks the controller.

| Wave | Task | Owns (create, modify or delete) |
|---|---|---|
| 1 | 1 | `frontend/src/api/mergeApprovals.ts` (+ `api/__tests__/mergeApprovals.test.ts`), `frontend/src/hooks/useMergeApprovals.ts`, `frontend/src/lib/mergeApprovals.ts`, `frontend/src/pages/{MergeApprovalsPage,MergeApprovalPage}.tsx` (+ tests), `frontend/src/components/ui/ConfirmDialog.tsx` (+ test), `frontend/src/components/dashboard/BriefBar.tsx` (+ test), `frontend/src/components/layout/navGroups.ts`, `frontend/src/components/layout/Breadcrumbs.tsx`, `frontend/src/components/layout/__tests__/{Sidebar,Breadcrumbs}.test.tsx`, `frontend/src/router/index.tsx`, `docs/merge-approvals.md`, `docs/glossary.md` (one entry) |
| 1 | 2 | `embry0/api/v1/pipeline_templates.py`, `embry0/api/schemas/__init__.py` (one docstring), `tests/unit/api/v1/test_pipeline_templates_api.py` |
| 1 | 3 | `frontend/src/lib/types/stats.ts`, `frontend/src/components/dashboard/{DashboardPanel,DeepStats}.tsx` (+ `__tests__/DeepStats.test.tsx`), `frontend/src/pages/DashboardPage.tsx` (+ test) |
| 1 | 4 | `frontend/src/api/qaDashboard.ts` (+ test), `frontend/src/components/qa/BuildImageButton.tsx` (+ test), `frontend/src/pages/{QaRepoCachePage,QaRepoDetailPage}.tsx` |
| 1 | 5 | `frontend/src/api/environment.ts`, `frontend/src/hooks/useEnvironments.ts`, `frontend/src/pages/RepoDetailPage.tsx` (+ test) |
| 2 | 6 | `frontend/src/lib/types/pipelines.ts`, `frontend/src/api/pipelines.ts` (+ test), `frontend/src/hooks/usePipelines.ts`, `frontend/src/components/pipelines/{TemplateActions,DuplicateTemplateDialog}.tsx` (+ tests), `frontend/src/pages/PipelinesPage.tsx`, `frontend/src/pages/__tests__/PipelinesPage*.test.tsx` |
| 2 | 7 | `frontend/src/api/hosts.ts` (+ test), `frontend/src/hooks/useHosts.ts`, `frontend/src/lib/hosts.ts`, `frontend/src/pages/HostsPage.tsx` (+ test), `frontend/src/components/hosts/HostFormDialog.tsx` (+ test), `frontend/src/components/layout/navGroups.ts`, `frontend/src/components/layout/Breadcrumbs.tsx`, `frontend/src/components/layout/__tests__/{Sidebar,Breadcrumbs}.test.tsx`, `frontend/src/router/index.tsx`, `docs/hosts.md`, `docs/glossary.md` (one entry) |
| 2 | 8 | `frontend/src/lib/types/watcher.ts`, `frontend/src/api/watcher.ts` (+ `api/__tests__/watcher.test.ts`), `frontend/src/hooks/useWatcherStacks.ts`, `frontend/src/lib/watcherTabs.ts`, `frontend/src/components/watcher/{WatcherStacksTab,StackEditorDialog}.tsx` (+ tests), `frontend/src/pages/WatcherPage.tsx` (+ test) |
| 2 | 9 | `frontend/src/api/orgs.ts` (+ test), `frontend/src/hooks/useOrgs.ts`, `frontend/src/lib/orgTabs.ts`, `frontend/src/components/orgs/OrgSettingsTab.tsx` (+ test), `frontend/src/pages/OrganizationPage.tsx` (+ its two tests) |

Wave 2 tasks import `ConfirmDialog` from Task 1 and Task 6 relies on Task 2's 409 and null semantics. The nav, breadcrumb and router files are touched by Task 1 (wave 1) and Task 7 (wave 2), never in the same wave.

---

### Task 1: Merge approvals, the list and the ask (issue P)

**Files:**
- Modify: `frontend/src/api/mergeApprovals.ts` (rewrite; keep `fetchPendingMergeApprovals` and its 503 reading)
- Create: `frontend/src/api/__tests__/mergeApprovals.test.ts`
- Modify: `frontend/src/hooks/useMergeApprovals.ts`
- Create: `frontend/src/lib/mergeApprovals.ts`
- Create: `frontend/src/components/ui/ConfirmDialog.tsx`, `frontend/src/components/ui/__tests__/ConfirmDialog.test.tsx`
- Create: `frontend/src/pages/MergeApprovalsPage.tsx`, `frontend/src/pages/MergeApprovalPage.tsx`, `frontend/src/pages/__tests__/MergeApprovalsPage.test.tsx`, `frontend/src/pages/__tests__/MergeApprovalPage.test.tsx`
- Modify: `frontend/src/components/dashboard/BriefBar.tsx` (the approvals item links to the page), `frontend/src/components/dashboard/__tests__/BriefBar.test.tsx`
- Modify: `frontend/src/components/layout/navGroups.ts` (Work gains Merge approvals after Jobs), `frontend/src/components/layout/Breadcrumbs.tsx` (`breadcrumbMap` gains `"merge-approvals"`), `frontend/src/components/layout/__tests__/Sidebar.test.tsx` (the Work order), `frontend/src/components/layout/__tests__/Breadcrumbs.test.tsx`
- Modify: `frontend/src/router/index.tsx` (two routes)
- Modify: `docs/merge-approvals.md` ("Deciding from the dashboard" names the page), `docs/glossary.md` (a `### MergeApprovalsPage` entry after `### SkillsPage`)

**Interfaces:**
- Consumes: `Dialog` (`@/components/ui/Dialog`), `AdminButton` (`@/components/auth/AdminButton`), `useIsAdmin` (`@/stores/authStore`), `FilterSelect`, `Badge`, `Button`, `EmptyState`, `PageError`, `TableSkeleton`, `Skeleton` (`@/components/ui/PageSkeleton`), `sentenceCase`, `statusLabel`, `statusTone` (`@/lib/status`), `formatDateTime` (`@/lib/utils`), `toastApiError`, `toast` from `sonner`.
- Produces, for Tasks 7, 8 and 9: `ConfirmDialog({ title, children, confirmLabel, destructive?, pending?, onConfirm, onClose })`. For the BriefBar: the approvals item's `to` is `/merge-approvals`.

The backend's row (`embry0/api/v1/merge_approvals.py`, `_serialize`): `id, repo, pr_number, pr_url, head_sha, job_id, case_id, status, dry_run, decided_by, decided_at, chat_id, detail, merge_sha, ask, posted_at, created_at, updated_at`; `ask` is built in `embry0/services/merge_approvals.py` around line 345 (`title, summary, review, head_ref, base_ref, additions, deletions, changed_files, denylisted, verification`, then `checks, checks_state, recommendation`); the list is `{"items": [...], "mode": "dry_run" | "on"}`; the decision answers `{outcome, status, dry_run, pr_url, head_sha}` and the dashboard form is `{"decision": "approve" | "reject"}` (plan 3a). Statuses: `pending|approved|rejected|merged|failed|stale|closed|expired`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/api/__tests__/mergeApprovals.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

import { api } from "../client";
import { decideMergeApproval, fetchMergeApproval, fetchMergeApprovals, fetchPendingMergeApprovals } from "../mergeApprovals";

const get = api.get as ReturnType<typeof vi.fn>;
const post = api.post as ReturnType<typeof vi.fn>;

beforeEach(() => vi.clearAllMocks());

describe("merge approvals api", () => {
  it("lists one status with the route's own filter and reads a 503 as the feature being off", async () => {
    get.mockResolvedValueOnce({ status: 200, data: { items: [{ id: "ma-1" }], mode: "dry_run" } });
    await expect(fetchMergeApprovals("pending")).resolves.toEqual({ items: [{ id: "ma-1" }], mode: "dry_run" });
    expect(get.mock.calls[0][1].params).toEqual({ status: "pending", limit: 200 });
    get.mockResolvedValueOnce({ status: 503, data: { detail: "off" } });
    await expect(fetchMergeApprovals("all")).resolves.toBeNull();
    expect(get.mock.calls[1][1].params).toEqual({ limit: 200 });
  });

  it("keeps the pending read the brief bar uses", async () => {
    get.mockResolvedValueOnce({ status: 200, data: { items: [{ id: "ma-1" }] } });
    await expect(fetchPendingMergeApprovals()).resolves.toEqual([{ id: "ma-1" }]);
  });

  it("reads one ask by id", async () => {
    get.mockResolvedValueOnce({ data: { id: "ma-1", repo: "acme/widgets" } });
    await expect(fetchMergeApproval("ma-1")).resolves.toEqual({ id: "ma-1", repo: "acme/widgets" });
    expect(get).toHaveBeenCalledWith("/merge-approvals/ma-1");
  });

  it("decides with the dashboard's form: the decision word alone", async () => {
    post.mockResolvedValueOnce({ data: { outcome: "rejected", status: "rejected", dry_run: true, pr_url: "", head_sha: "" } });
    await expect(decideMergeApproval("ma-1", "reject")).resolves.toMatchObject({ outcome: "rejected" });
    expect(post).toHaveBeenCalledWith("/merge-approvals/ma-1/decide", { decision: "reject" }, expect.anything());
  });
});
```

`frontend/src/components/ui/__tests__/ConfirmDialog.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "../ConfirmDialog";

describe("ConfirmDialog", () => {
  it("names itself by its title, puts focus on Cancel and confirms on the labelled button", async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <ConfirmDialog title="Remove web-1?" confirmLabel="Remove" destructive onConfirm={onConfirm} onClose={onClose}>
        Its repository links are dropped.
      </ConfirmDialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Remove web-1?" });
    expect(dialog).toHaveTextContent("Its repository links are dropped.");
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("disables both buttons while pending and says so", () => {
    render(
      <ConfirmDialog title="Import" confirmLabel="Import" pending onConfirm={vi.fn()} onClose={vi.fn()}>
        body
      </ConfirmDialog>,
    );
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("does not close on a backdrop click", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog title="Sure?" confirmLabel="Yes" onConfirm={vi.fn()} onClose={onClose}>
        body
      </ConfirmDialog>,
    );
    const dialog = screen.getByRole("dialog");
    await userEvent.click(dialog.parentElement as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });
});
```

`frontend/src/pages/__tests__/MergeApprovalsPage.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

const mockUseMergeApprovals = vi.fn();
vi.mock("@/hooks/useMergeApprovals", () => ({
  useMergeApprovals: (status: string) => mockUseMergeApprovals(status),
}));

import { MergeApprovalsPage } from "../MergeApprovalsPage";

const ROW = {
  id: "ma-1",
  repo: "acme/widgets",
  pr_number: 42,
  pr_url: "https://github.com/acme/widgets/pull/42",
  head_sha: "abc123def456",
  job_id: "job-1",
  case_id: null,
  status: "pending",
  dry_run: true,
  decided_by: null,
  decided_at: null,
  chat_id: null,
  detail: null,
  merge_sha: null,
  ask: {
    title: "Fix the widget",
    summary: "One file.",
    review: null,
    head_ref: "embry0/fix",
    base_ref: "main",
    additions: 3,
    deletions: 1,
    changed_files: 1,
    denylisted: {},
    verification: { present: false },
    checks: "all 2 checks passed",
    checks_state: "green",
    recommendation: { verdict: "merge", reasons: [], positives: ["checks green"], notes: [] },
  },
  posted_at: "2026-10-20T10:00:00Z",
  created_at: "2026-10-20T10:00:00Z",
  updated_at: "2026-10-20T10:00:00Z",
};

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MergeApprovalsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockUseMergeApprovals.mockReset().mockReturnValue(ready({ items: [ROW], mode: "dry_run" }));
});

describe("MergeApprovalsPage", () => {
  it("lists each ask with its PR, title, recommendation and a link to its own page", () => {
    renderPage();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("link", { name: "acme/widgets#42" })).toHaveAttribute("href", ROW.pr_url);
    expect(within(table).getByText("Fix the widget")).toBeInTheDocument();
    expect(within(table).getByText("Merge")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Details" })).toHaveAttribute("href", "/merge-approvals/ma-1");
  });

  it("opens on the pending asks and refilters through the hook", async () => {
    renderPage();
    expect(mockUseMergeApprovals).toHaveBeenLastCalledWith("pending");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Statuses" }), "merged");
    expect(mockUseMergeApprovals).toHaveBeenLastCalledWith("merged");
  });

  it("says the deployment is in dry run when the list says so", () => {
    renderPage();
    expect(screen.getByText(/dry run/i)).toBeInTheDocument();
  });

  it("shows an honest empty state, and a different one when the feature is off", () => {
    mockUseMergeApprovals.mockReturnValue(ready({ items: [], mode: "on" }));
    renderPage();
    expect(screen.getByText("No pending merge approvals")).toBeInTheDocument();
    mockUseMergeApprovals.mockReturnValue(ready(null));
    renderPage();
    expect(screen.getByText("Merge approvals are off on this deployment")).toBeInTheDocument();
  });

  it("shows the error state with a retry", () => {
    const refetch = vi.fn();
    mockUseMergeApprovals.mockReturnValue({ data: undefined, isPending: false, isError: true, refetch });
    renderPage();
    expect(screen.getByText("Failed to load merge approvals")).toBeInTheDocument();
  });
});
```

`frontend/src/pages/__tests__/MergeApprovalPage.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { useAuthStore } from "@/stores/authStore";

const mockUseMergeApproval = vi.fn();
const mutate = vi.fn();
vi.mock("@/hooks/useMergeApprovals", () => ({
  useMergeApproval: (id: string | null) => mockUseMergeApproval(id),
  useDecideMergeApproval: () => ({ mutate, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { MergeApprovalPage } from "../MergeApprovalPage";

const ASK = {
  title: "Fix the widget",
  summary: "One file changed.",
  review: { decision: "approve", validation: {}, critical: 0, important: 1, addresses_failure: "yes", duplicate_pr: null, summary: "Looks right." },
  head_ref: "embry0/fix",
  base_ref: "main",
  additions: 3,
  deletions: 1,
  changed_files: 1,
  denylisted: { secrets: { count: 1, paths: [".env.example"] } },
  verification: { present: true, mode: "full", gate_verdict: "pass", blocking: false, human_merge_only: false, human_merge_reasons: [], for_this_head: true },
  checks: "all 2 checks passed",
  checks_state: "green",
  recommendation: { verdict: "review_first", reasons: ["a denylisted path"], positives: ["checks green"], notes: [] },
};

const ROW = {
  id: "ma-1",
  repo: "acme/widgets",
  pr_number: 42,
  pr_url: "https://github.com/acme/widgets/pull/42",
  head_sha: "abc123def456",
  job_id: "job-1",
  case_id: null,
  status: "pending",
  dry_run: false,
  decided_by: null,
  decided_at: null,
  chat_id: null,
  detail: null,
  merge_sha: null,
  ask: ASK,
  posted_at: "2026-10-20T10:00:00Z",
  created_at: "2026-10-20T10:00:00Z",
  updated_at: "2026-10-20T10:00:00Z",
};

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, error: null, refetch: vi.fn() };
}

function renderPage(path = "/merge-approvals/ma-1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/merge-approvals/:approvalId" element={<MergeApprovalPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mutate.mockReset();
  mockUseMergeApproval.mockReset().mockReturnValue(ready(ROW));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("MergeApprovalPage", () => {
  it("names the ask and shows the change, the recommendation, the checks, the review and the denylisted paths", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "acme/widgets#42" })).toBeInTheDocument();
    expect(screen.getByText("Fix the widget")).toBeInTheDocument();
    expect(screen.getByText("embry0/fix → main")).toBeInTheDocument();
    expect(screen.getByText("Review first")).toBeInTheDocument();
    expect(screen.getByText("a denylisted path")).toBeInTheDocument();
    expect(screen.getByText("all 2 checks passed")).toBeInTheDocument();
    expect(screen.getByText("Looks right.")).toBeInTheDocument();
    expect(screen.getByText(".env.example")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "job-1" })).toHaveAttribute("href", "/jobs/job-1");
  });

  it("approves through a confirm dialog with the dashboard's form", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Approve acme/widgets#42?" });
    expect(dialog).toHaveTextContent(/merges the pull request/i);
    await userEvent.click(screen.getByRole("button", { name: "Approve and merge" }));
    expect(mutate).toHaveBeenCalledWith({ id: "ma-1", decision: "approve" }, expect.anything());
  });

  it("rejects through its own confirm dialog", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject the ask" }));
    expect(mutate).toHaveBeenCalledWith({ id: "ma-1", decision: "reject" }, expect.anything());
  });

  it("offers the decision disabled to an operator session", () => {
    useAuthStore.setState({ authMode: "session", user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    renderPage();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("shows the outcome when the ask was decided elsewhere first", async () => {
    mutate.mockImplementation((_vars: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ outcome: "already decided", status: "approved", dry_run: false, pr_url: ROW.pr_url, head_sha: ROW.head_sha }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and merge" }));
    expect(toast.info).toHaveBeenCalledWith("acme/widgets#42: Already decided");
  });

  it("shows who decided and hides the buttons once decided", () => {
    mockUseMergeApproval.mockReturnValue(
      ready({ ...ROW, status: "merged", decided_by: "ops@example.test", decided_at: "2026-10-20T11:00:00Z", chat_id: "dashboard", merge_sha: "fed", detail: "merged" }),
    );
    renderPage();
    expect(screen.getByText(/decided by ops@example.test/i)).toBeInTheDocument();
    expect(screen.getByText(/from the dashboard/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("shows a way back for an ask that does not exist", () => {
    mockUseMergeApproval.mockReturnValue({ data: undefined, isPending: false, isError: true, error: { response: { status: 404 } }, refetch: vi.fn() });
    renderPage("/merge-approvals/ma-9");
    expect(screen.getByText("No merge approval ma-9")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All merge approvals" })).toHaveAttribute("href", "/merge-approvals");
  });
});
```

Add to `frontend/src/components/dashboard/__tests__/BriefBar.test.tsx`, in the first test after the approvals text assertion:

```tsx
    expect(screen.getByTestId("brief-approvals")).toHaveAttribute("href", "/merge-approvals");
```

Change `frontend/src/components/layout/__tests__/Sidebar.test.tsx`: the Work test's title becomes `"Work section contains Console, Issues, Jobs, Merge approvals in that order"` and its expected array gains `"Merge approvals"` last.

Add to `frontend/src/components/layout/__tests__/Breadcrumbs.test.tsx`:

```tsx
  it("names the Work section on the merge approvals list and the ask under it", () => {
    renderAt("/merge-approvals");
    expect(trail()).toBe("Work");
    renderAt("/merge-approvals/ma-1");
    expect(screen.getAllByRole("navigation", { name: "Breadcrumb" })[1]).toHaveTextContent("Merge approvals/ma-1");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/api/__tests__/mergeApprovals.test.ts src/components/ui/__tests__/ConfirmDialog.test.tsx src/pages/__tests__/MergeApprovalsPage.test.tsx src/pages/__tests__/MergeApprovalPage.test.tsx src/components/dashboard/__tests__/BriefBar.test.tsx src/components/layout/__tests__/Sidebar.test.tsx src/components/layout/__tests__/Breadcrumbs.test.tsx`
Expected: the four new files fail to resolve their modules; the BriefBar test fails on `href`; the Sidebar test fails on the Work array; the Breadcrumbs test fails on `"Work"` (the trail reads `embry0`).

- [ ] **Step 3: Write the api module, the hook, the labels and the ConfirmDialog**

`frontend/src/api/mergeApprovals.ts`:

```ts
import { api } from "./client";

export const MERGE_APPROVAL_STATUSES = [
  "pending",
  "approved",
  "rejected",
  "merged",
  "failed",
  "stale",
  "closed",
  "expired",
] as const;
export type MergeApprovalStatus = (typeof MERGE_APPROVAL_STATUSES)[number];
export type MergeVerdict = "merge" | "review_first" | "dont_merge";
export type ChecksState = "green" | "pending" | "red" | "unknown";
export type MergeDecision = "approve" | "reject";

/** The reviewer agent's verdict as the ask carries it. */
export interface MergeReviewFacts {
  decision: string;
  validation: Record<string, string>;
  critical: number;
  important: number;
  addresses_failure: string;
  duplicate_pr: number | null;
  summary: string;
}

export interface MergeRecommendation {
  verdict: MergeVerdict;
  reasons: string[];
  positives: string[];
  notes: string[];
}

/** The verification gate's summary for the PR's head; `present: false` when the job has no record. */
export interface MergeVerification {
  present: boolean;
  mode?: string | null;
  gate_verdict?: string | null;
  blocking?: boolean;
  human_merge_only?: boolean;
  human_merge_reasons?: string[];
  for_this_head?: boolean;
}

export interface MergeAsk {
  title: string;
  summary: string;
  review: MergeReviewFacts | null;
  head_ref: string;
  base_ref: string;
  additions: number;
  deletions: number;
  changed_files: number;
  denylisted: Record<string, { count: number; paths: string[] }>;
  verification: MergeVerification;
  /** The CI state in a sentence, its class, and the fixed-rule recommendation; absent on an ask written before they were. */
  checks?: string;
  checks_state?: ChecksState;
  recommendation?: MergeRecommendation;
}

/** One merge ask (GET /merge-approvals). */
export interface MergeApproval {
  id: string;
  repo: string;
  pr_number: number;
  pr_url: string;
  head_sha: string;
  job_id: string | null;
  case_id: string | null;
  status: MergeApprovalStatus | string;
  dry_run: boolean;
  decided_by: string | null;
  decided_at: string | null;
  /** The chat the answer came from, or the literal `dashboard`. */
  chat_id: string | null;
  detail: string | null;
  merge_sha: string | null;
  ask: MergeAsk | null;
  posted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MergeApprovalList {
  items: MergeApproval[];
  /** `dry_run` records decisions and merges nothing; `on` merges. */
  mode?: "dry_run" | "on";
}

/** What POST /merge-approvals/{id}/decide answers. */
export interface MergeDecisionResult {
  outcome: string;
  status: string;
  dry_run: boolean;
  pr_url: string;
  head_sha: string;
}

const FEATURE_OFF = (status: number) => (status >= 200 && status < 300) || status === 503;

/**
 * The asks, newest first, in one status or all of them; `null` when this
 * deployment has merge approvals off (the route answers 503). The 503 is read
 * as an answer, not an error, so the page shows an empty state and never
 * toasts a server error for a feature the operator did not turn on.
 */
export async function fetchMergeApprovals(status: MergeApprovalStatus | "all"): Promise<MergeApprovalList | null> {
  const response = await api.get<MergeApprovalList>("/merge-approvals", {
    params: status === "all" ? { limit: 200 } : { status, limit: 200 },
    validateStatus: FEATURE_OFF,
  });
  if (response.status === 503) return null;
  return response.data;
}

/** The asks still waiting for an answer, or `null` when the feature is off (the brief bar's read). */
export async function fetchPendingMergeApprovals(): Promise<MergeApproval[] | null> {
  const list = await fetchMergeApprovals("pending");
  return list === null ? null : list.items;
}

export async function fetchMergeApproval(approvalId: string): Promise<MergeApproval> {
  const { data } = await api.get<MergeApproval>(`/merge-approvals/${encodeURIComponent(approvalId)}`);
  return data;
}

/**
 * The dashboard's form of the decision route (spec 3.8): the decision word
 * alone. The server names the actor from the session or key and refuses
 * anyone below admin with a 403 the page explains itself.
 */
export async function decideMergeApproval(approvalId: string, decision: MergeDecision): Promise<MergeDecisionResult> {
  const { data } = await api.post<MergeDecisionResult>(
    `/merge-approvals/${encodeURIComponent(approvalId)}/decide`,
    { decision },
    { suppressValidationToast: true },
  );
  return data;
}
```

`frontend/src/hooks/useMergeApprovals.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  decideMergeApproval,
  fetchMergeApproval,
  fetchMergeApprovals,
  fetchPendingMergeApprovals,
  type MergeApprovalStatus,
  type MergeDecision,
} from "@/api/mergeApprovals";

/** Pending merge asks; `data === null` when the deployment has the feature off. */
export function usePendingMergeApprovals() {
  return useQuery({
    queryKey: ["merge-approvals", "pending"],
    queryFn: fetchPendingMergeApprovals,
    refetchInterval: 30_000,
    retry: false,
  });
}

/** The asks in one status or all; `data === null` when the feature is off. */
export function useMergeApprovals(status: MergeApprovalStatus | "all") {
  return useQuery({
    queryKey: ["merge-approvals", "list", status],
    queryFn: () => fetchMergeApprovals(status),
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useMergeApproval(approvalId: string | null) {
  return useQuery({
    queryKey: ["merge-approvals", "detail", approvalId],
    queryFn: () => fetchMergeApproval(approvalId!),
    enabled: !!approvalId,
    retry: false,
  });
}

/** Decide one ask; every merge-approval read is stale afterwards, the brief bar's count included. */
export function useDecideMergeApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: MergeDecision }) => decideMergeApproval(id, decision),
    onSettled: () => qc.invalidateQueries({ queryKey: ["merge-approvals"] }),
  });
}
```

`frontend/src/lib/mergeApprovals.ts`:

```ts
import type { BadgeTone } from "@/components/ui/Badge";
import type { ChecksState, MergeApproval, MergeVerdict } from "@/api/mergeApprovals";

export const VERDICT_LABELS: Readonly<Record<MergeVerdict, string>> = {
  merge: "Merge",
  review_first: "Review first",
  dont_merge: "Don't merge",
};

export const VERDICT_TONES: Readonly<Record<MergeVerdict, BadgeTone>> = {
  merge: "success",
  review_first: "warning",
  dont_merge: "error",
};

export const CHECKS_TONES: Readonly<Record<ChecksState, BadgeTone>> = {
  green: "success",
  pending: "info",
  red: "error",
  unknown: "neutral",
};

/** How an ask is named everywhere: the repository and the PR number, as GitHub writes them. */
export function approvalTitle(row: Pick<MergeApproval, "repo" | "pr_number">): string {
  return `${row.repo}#${row.pr_number}`;
}

/** Where the decision came from, for a person: the dashboard, or the chat. */
export function decidedVia(chatId: string | null): string {
  return chatId === "dashboard" ? "from the dashboard" : "in the approval chat";
}
```

`frontend/src/components/ui/ConfirmDialog.tsx`:

```tsx
import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";

export interface ConfirmDialogProps {
  /** A question, which is also the dialog's name: "Remove web-1?". */
  title: string;
  /** What the action does and what it costs, in a sentence or two. */
  children: ReactNode;
  confirmLabel: string;
  /** A destructive action's button is red; the default is the page's primary. */
  destructive?: boolean;
  /** While the action runs both buttons are disabled and the confirm says so. */
  pending?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * The one "are you sure" (spec 3.7): a Dialog that must be answered, so the
 * backdrop does not dismiss it; Escape and Cancel do. Focus lands on Cancel,
 * so a stray Enter never confirms. It opens from a page, a row or a tab, never
 * from inside another Dialog.
 */
export function ConfirmDialog({ title, children, confirmLabel, destructive, pending, onConfirm, onClose }: ConfirmDialogProps) {
  return (
    <Dialog onClose={onClose} title={title} dismissOnBackdrop={false} className="max-w-md">
      <div className="space-y-4 p-5">
        <div className="text-sm text-fg-secondary">{children}</div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending} data-autofocus>
            Cancel
          </Button>
          <Button type="button" variant={destructive ? "destructive" : "default"} onClick={onConfirm} disabled={pending}>
            {pending ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
```

- [ ] **Step 4: Write the two pages**

`frontend/src/pages/MergeApprovalsPage.tsx`:

```tsx
import { useState } from "react";
import { Link } from "react-router";
import { GitMerge } from "lucide-react";
import { MERGE_APPROVAL_STATUSES, type MergeApproval, type MergeApprovalStatus } from "@/api/mergeApprovals";
import { useMergeApprovals } from "@/hooks/useMergeApprovals";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { CHECKS_TONES, VERDICT_LABELS, VERDICT_TONES, approvalTitle } from "@/lib/mergeApprovals";
import { sentenceCase, statusLabel, statusTone } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";

function RecommendationBadge({ row }: { row: MergeApproval }) {
  const verdict = row.ask?.recommendation?.verdict;
  if (!verdict) return <span className="text-fg-muted">—</span>;
  return <Badge tone={VERDICT_TONES[verdict]}>{VERDICT_LABELS[verdict]}</Badge>;
}

function ChecksBadge({ row }: { row: MergeApproval }) {
  const state = row.ask?.checks_state;
  if (!state) return <span className="text-fg-muted">—</span>;
  return <Badge tone={CHECKS_TONES[state]}>{sentenceCase(state)}</Badge>;
}

/**
 * Merge approvals (spec 3.7): the asks embry0 posted for a person to answer,
 * one status at a time, pending first. Each row leads to the ask's own page,
 * where the decision is made. A deployment with the feature off gets an empty
 * state, not an error: the route's 503 is read as "off".
 */
export function MergeApprovalsPage() {
  const [status, setStatus] = useState<MergeApprovalStatus | "all">("pending");
  const list = useMergeApprovals(status);

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load merge approvals" onRetry={() => void list.refetch()} />;
  } else if (list.isPending) {
    body = <TableSkeleton />;
  } else if (list.data === null) {
    body = (
      <EmptyState
        icon={GitMerge}
        title="Merge approvals are off on this deployment"
        description="Set MERGE_APPROVAL_MODE and the approval chat in the root .env to have embry0 ask before it merges."
      />
    );
  } else if (list.data.items.length === 0) {
    body = (
      <EmptyState
        icon={GitMerge}
        title={status === "all" ? "No merge approvals yet" : `No ${statusLabel(status).toLowerCase()} merge approvals`}
        description="An ask appears here when a reviewed pull request waits for a person to approve its merge."
      />
    );
  } else {
    body = (
      // On a phone the checks, who decided and when step aside; the PR, its
      // title, the recommendation and the status stay.
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Pull request</th>
              <th scope="col">Title</th>
              <th scope="col">Recommendation</th>
              <th scope="col" className="hidden sm:table-cell">Checks</th>
              <th scope="col">Status</th>
              <th scope="col" className="hidden sm:table-cell">Decided by</th>
              <th scope="col" className="num hidden sm:table-cell">Asked</th>
              <th scope="col"><span className="sr-only">Details</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.items.map((row) => (
              <tr key={row.id} data-testid="merge-approval-row">
                <td className="whitespace-nowrap">
                  <a href={row.pr_url} target="_blank" rel="noreferrer" className="font-mono text-xs text-fg hover:text-primary hover:underline">
                    {approvalTitle(row)}
                  </a>
                </td>
                <td className="w-full max-w-0 truncate" title={row.ask?.title}>
                  {row.ask?.title || <span className="text-fg-muted">—</span>}
                </td>
                <td className="whitespace-nowrap"><RecommendationBadge row={row} /></td>
                <td className="hidden whitespace-nowrap sm:table-cell"><ChecksBadge row={row} /></td>
                <td className="whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <Badge tone={statusTone(row.status)}>{statusLabel(row.status)}</Badge>
                    {row.dry_run && <Badge tone="neutral">Dry run</Badge>}
                  </span>
                </td>
                <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{row.decided_by ?? "—"}</td>
                <td className="num hidden whitespace-nowrap text-fg-muted sm:table-cell">{formatDateTime(row.posted_at ?? row.created_at)}</td>
                <td className="whitespace-nowrap">
                  <Link to={`/merge-approvals/${encodeURIComponent(row.id)}`} className="text-sm text-fg hover:text-primary hover:underline">
                    Details
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-pixel text-xl font-bold">Merge approvals</h1>
          <p className="text-sm text-muted-foreground">
            Reviewed pull requests waiting for a person to approve the merge, and how each was answered.
          </p>
        </div>
        <FilterSelect
          label="Statuses"
          value={status === "all" ? undefined : status}
          options={[...MERGE_APPROVAL_STATUSES]}
          onChange={(v) => setStatus((v as MergeApprovalStatus | undefined) ?? "all")}
          optionLabel={statusLabel}
        />
      </div>
      {list.data?.mode === "dry_run" && (
        <p className="text-sm text-fg-secondary" data-testid="merge-approvals-mode">
          This deployment is in dry run: decisions are recorded and nothing merges.
        </p>
      )}
      {body}
    </div>
  );
}
```

`frontend/src/pages/MergeApprovalPage.tsx`:

```tsx
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import type { MergeApproval, MergeDecision } from "@/api/mergeApprovals";
import { isNotFound } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { useDecideMergeApproval, useMergeApproval } from "@/hooks/useMergeApprovals";
import { AdminButton } from "@/components/auth/AdminButton";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { PageError } from "@/components/PageError";
import { CHECKS_TONES, VERDICT_LABELS, VERDICT_TONES, approvalTitle, decidedVia } from "@/lib/mergeApprovals";
import { sentenceCase, statusLabel, statusTone } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2 rounded-lg border border-border bg-card p-4">
      <h2 className="text-section font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Lines({ items, className }: { items: string[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <ul className={className ?? "list-disc space-y-1 pl-5 text-sm text-fg-secondary"}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function Decision({ row }: { row: MergeApproval }) {
  const [asking, setAsking] = useState<MergeDecision | null>(null);
  const decide = useDecideMergeApproval();
  const title = approvalTitle(row);

  if (row.status !== "pending") {
    return (
      <p className="text-sm text-fg-secondary" data-testid="merge-approval-decided">
        {row.decided_by
          ? `Decided by ${row.decided_by} ${decidedVia(row.chat_id)}${row.decided_at ? ` on ${formatDateTime(row.decided_at)}` : ""}.`
          : `${statusLabel(row.status)}.`}
        {row.detail && ` ${sentenceCase(row.detail)}.`}
      </p>
    );
  }

  const submit = (decision: MergeDecision) => {
    decide.mutate(
      { id: row.id, decision },
      {
        onSuccess: (result) => {
          setAsking(null);
          toast.info(`${title}: ${sentenceCase(result.outcome)}`);
        },
        onError: (e) => {
          setAsking(null);
          toastApiError(e, "The decision was not recorded");
        },
      },
    );
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <AdminButton type="button" onClick={() => setAsking("approve")} disabled={decide.isPending}>
          Approve
        </AdminButton>
        <AdminButton type="button" variant="outline" onClick={() => setAsking("reject")} disabled={decide.isPending}>
          Reject
        </AdminButton>
        {row.dry_run && <span className="text-sm text-fg-muted">Dry run: an approval is recorded and nothing merges.</span>}
      </div>
      {asking === "approve" && (
        <ConfirmDialog
          title={`Approve ${title}?`}
          confirmLabel="Approve and merge"
          pending={decide.isPending}
          onConfirm={() => submit("approve")}
          onClose={() => setAsking(null)}
        >
          {row.dry_run
            ? "Records the approval. In dry run nothing merges."
            : "Re-checks the pull request (same head, checks green, verification not blocking) and merges the pull request."}
        </ConfirmDialog>
      )}
      {asking === "reject" && (
        <ConfirmDialog
          title={`Reject ${title}?`}
          confirmLabel="Reject the ask"
          destructive
          pending={decide.isPending}
          onConfirm={() => submit("reject")}
          onClose={() => setAsking(null)}
        >
          The pull request stays open and embry0 does not merge it. A new ask needs a new push.
        </ConfirmDialog>
      )}
    </>
  );
}

/** One merge ask (spec 3.7): what embry0 found, what it recommends, and the decision. */
export function MergeApprovalPage() {
  const approvalId = useParams().approvalId ?? "";
  const ask = useMergeApproval(approvalId || null);

  if (ask.isError) {
    if (isNotFound(ask.error)) {
      return <PageError message={`No merge approval ${approvalId}`} back={{ to: "/merge-approvals", label: "All merge approvals" }} />;
    }
    return <PageError message={`Failed to load merge approval ${approvalId}`} onRetry={() => void ask.refetch()} />;
  }
  if (ask.isPending || !ask.data) return <DashboardSkeleton />;

  const row = ask.data;
  const a = row.ask;
  const recommendation = a?.recommendation;
  const verification = a?.verification;

  return (
    <div className="space-y-6" data-testid="merge-approval-page">
      <div className="space-y-2">
        <Link to="/merge-approvals" className="text-sm text-white/50 hover:underline">
          ← Merge approvals
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="title-pixel text-xl font-bold">{approvalTitle(row)}</h1>
          <Badge tone={statusTone(row.status)}>{statusLabel(row.status)}</Badge>
          {row.dry_run && <Badge tone="neutral">Dry run</Badge>}
          {recommendation && <Badge tone={VERDICT_TONES[recommendation.verdict]}>{VERDICT_LABELS[recommendation.verdict]}</Badge>}
        </div>
        {a && <p className="text-sm text-fg">{a.title}</p>}
        <p className="text-sm text-fg-secondary">
          <a href={row.pr_url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">
            Open the pull request
          </a>
          {row.job_id && (
            <>
              {" · job "}
              <Link to={`/jobs/${row.job_id}`} className="font-mono text-xs hover:text-primary hover:underline">
                {row.job_id}
              </Link>
            </>
          )}
          {` · asked ${formatDateTime(row.posted_at ?? row.created_at)}`}
        </p>
        <Decision row={row} />
      </div>

      {a ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Section title="Summary">
            <p className="text-sm text-fg-secondary">{a.summary || "No summary."}</p>
            <p className="text-sm text-fg-secondary">
              <span className="font-mono text-xs">{`${a.head_ref} → ${a.base_ref}`}</span>
              {` · +${a.additions} −${a.deletions} in ${a.changed_files} ${a.changed_files === 1 ? "file" : "files"}`}
            </p>
          </Section>

          <Section title="Recommendation">
            {recommendation ? (
              <>
                <Badge tone={VERDICT_TONES[recommendation.verdict]}>{VERDICT_LABELS[recommendation.verdict]}</Badge>
                <Lines items={recommendation.reasons} />
                <Lines items={recommendation.positives} className="list-disc space-y-1 pl-5 text-sm text-fg-muted" />
                <Lines items={recommendation.notes} className="space-y-1 text-xs text-fg-muted" />
              </>
            ) : (
              <p className="text-sm text-fg-muted">This ask carries no recommendation.</p>
            )}
          </Section>

          <Section title="Checks">
            {a.checks_state ? (
              <p className="flex flex-wrap items-center gap-2 text-sm text-fg-secondary">
                <Badge tone={CHECKS_TONES[a.checks_state]}>{sentenceCase(a.checks_state)}</Badge>
                <span>{a.checks}</span>
              </p>
            ) : (
              <p className="text-sm text-fg-muted">The CI state was not recorded.</p>
            )}
            {verification?.present ? (
              <p className="text-sm text-fg-secondary">
                {`Verification ${verification.gate_verdict ?? "unknown"}`}
                {verification.blocking && ", blocking"}
                {verification.human_merge_only && ", human merge only"}
                {verification.for_this_head === false && " (for an earlier head)"}
                {"."}
              </p>
            ) : (
              <p className="text-sm text-fg-muted">No verification record for this job.</p>
            )}
            {verification?.human_merge_reasons && <Lines items={verification.human_merge_reasons} />}
          </Section>

          <Section title="Review">
            {a.review ? (
              <>
                <p className="text-sm text-fg-secondary">
                  {`${sentenceCase(a.review.decision || "no decision")} · ${a.review.critical} critical, ${a.review.important} important`}
                  {a.review.addresses_failure && ` · addresses the failure: ${a.review.addresses_failure}`}
                  {a.review.duplicate_pr !== null && ` · duplicate of #${a.review.duplicate_pr}`}
                </p>
                {a.review.summary && <p className="text-sm text-fg-secondary">{a.review.summary}</p>}
              </>
            ) : (
              <p className="text-sm text-fg-muted">No reviewer verdict on this pull request.</p>
            )}
          </Section>

          {Object.keys(a.denylisted).length > 0 && (
            <Section title="Denylisted paths">
              {Object.entries(a.denylisted).map(([category, hit]) => (
                <div key={category} className="space-y-1">
                  <p className="text-sm text-fg-secondary">{`${sentenceCase(category)} · ${hit.count}`}</p>
                  <ul className="space-y-0.5 font-mono text-xs text-fg-muted">
                    {hit.paths.map((path) => (
                      <li key={path}>{path}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </Section>
          )}
        </div>
      ) : (
        <p className="text-sm text-fg-muted">This ask was recorded without its details.</p>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Wire the nav, the breadcrumbs, the router, the brief bar and the docs**

`frontend/src/components/layout/navGroups.ts`: import `GitMerge` from lucide and add `{ path: "/merge-approvals", label: "Merge approvals", icon: GitMerge },` after the Jobs entry of the Work group.

`frontend/src/components/layout/Breadcrumbs.tsx`: add `"merge-approvals": "Merge approvals",` to `breadcrumbMap` after `jobs`.

`frontend/src/router/index.tsx`: add the lazy imports

```tsx
const MergeApprovalsPage = lazy(() => import("../pages/MergeApprovalsPage").then(m => ({ default: m.MergeApprovalsPage })));
const MergeApprovalPage = lazy(() => import("../pages/MergeApprovalPage").then(m => ({ default: m.MergeApprovalPage })));
```

and the routes after `jobs/:jobId/logs`:

```tsx
      { path: "merge-approvals", element: <ErrorBoundary inline><Suspense fallback={fallback}><MergeApprovalsPage /></Suspense></ErrorBoundary> },
      { path: "merge-approvals/:approvalId", element: <ErrorBoundary inline><Suspense fallback={fallback}><MergeApprovalPage /></Suspense></ErrorBoundary> },
```

`frontend/src/components/dashboard/BriefBar.tsx`: the approvals item gains `to: "/merge-approvals"`; the `BriefItem.to` doc line becomes `/** Where the number is answered. */`; the `span` branch loses its `title="Answered in the approval chat"` (every item now links, the branch stays for a source that has no page).

`docs/merge-approvals.md`, in "Deciding from the dashboard", after the first paragraph: `The page is **Merge approvals** in the Work section: the asks by status, pending first, and each ask's own page with the change, the checks, the reviewer's verdict, the recommendation and the two buttons.`

`docs/glossary.md`, after the `### SkillsPage` entry:

```markdown
### MergeApprovalsPage
The dashboard page at `/merge-approvals` (Work) that lists the merge asks ([merge-approvals.md](merge-approvals.md)) by status, pending first; each ask has its own page at `/merge-approvals/{id}` with the change, the CI checks, the verification gate, the reviewer's verdict, the recommendation and, for an admin, Approve and Reject behind a `ConfirmDialog`. The decision uses the dashboard form of `POST /merge-approvals/{id}/decide` (spec 3.8).
```

- [ ] **Step 6: Run the tests to verify they pass, then the gate**

Run the Step 2 command: every file passes. Then the frontend gate (`npm run build`, `npm run lint`, `npx vitest run`); read the summary lines.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/mergeApprovals.ts frontend/src/api/__tests__/mergeApprovals.test.ts frontend/src/hooks/useMergeApprovals.ts frontend/src/lib/mergeApprovals.ts frontend/src/components/ui/ConfirmDialog.tsx frontend/src/components/ui/__tests__/ConfirmDialog.test.tsx frontend/src/pages/MergeApprovalsPage.tsx frontend/src/pages/MergeApprovalPage.tsx frontend/src/pages/__tests__/MergeApprovalsPage.test.tsx frontend/src/pages/__tests__/MergeApprovalPage.test.tsx frontend/src/components/dashboard/BriefBar.tsx frontend/src/components/dashboard/__tests__/BriefBar.test.tsx frontend/src/components/layout/navGroups.ts frontend/src/components/layout/Breadcrumbs.tsx frontend/src/components/layout/__tests__/Sidebar.test.tsx frontend/src/components/layout/__tests__/Breadcrumbs.test.tsx frontend/src/router/index.tsx docs/merge-approvals.md docs/glossary.md
git commit -m "feat(dashboard): merge approvals page, the ask's page and a ConfirmDialog

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 2: Backend, a template's default kind can be cleared and a clash is 409

**Files:**
- Modify: `embry0/api/v1/pipeline_templates.py` (`create_template` and `update_template`)
- Modify: `embry0/api/schemas/__init__.py` (the `default_for_kind` field description of `TemplateUpdateRequest`, line 534)
- Modify: `tests/unit/api/v1/test_pipeline_templates_api.py` (append)

**Interfaces:**
- Consumes: `PipelineTemplatesRepository.update(template_id, **fields)` passes every key in `_UPDATABLE_FIELDS` through, a `None` value included (`embry0/storage/repositories/pipeline_templates.py:64`), so `default_for_kind=None` writes `NULL`. The partial unique index `ON pipeline_templates (default_for_kind) WHERE default_for_kind IS NOT NULL` (migration in `embry0/storage/migrations/runner.py:1026`) raises `asyncpg.UniqueViolationError` on a second holder.
- Produces, for Task 6: `PUT /pipelines/templates/{id}` with `{"default_for_kind": null}` clears the default and answers the template; a body without the field keeps it; a kind another template holds answers `409 {"detail": "another template is already the default for 'code'; clear it first"}` on `PUT` and on `POST /pipelines/templates`.

Today `update_template` builds `updates = {k: v for k, v in req.model_dump().items() if v is not None}`, so a null is indistinguishable from an absent field, and nothing catches the unique violation, which surfaces as a 500. The fix reads `req.model_fields_set` for the one field whose null means something.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/api/v1/test_pipeline_templates_api.py`:

```python
@pytest.mark.asyncio
async def test_an_explicit_null_clears_the_default_kind(app):
    """``{"default_for_kind": null}`` is a write, not an absent field (spec 3.7, plan 3b)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/api/v1/pipelines/templates/tmpl-1", json={"default_for_kind": None})
    assert resp.status_code == 200, resp.text
    app.state.templates_repo.update.assert_awaited_once_with("tmpl-1", default_for_kind=None)


@pytest.mark.asyncio
async def test_an_absent_default_kind_is_kept(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/api/v1/pipelines/templates/tmpl-1", json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text
    app.state.templates_repo.update.assert_awaited_once_with("tmpl-1", name="Renamed")


@pytest.mark.asyncio
async def test_a_second_default_for_a_kind_is_409(app):
    """The partial unique index refuses two defaults for one kind; the route says which kind, not a 500."""
    app.state.templates_repo.update = AsyncMock(side_effect=asyncpg.UniqueViolationError("duplicate key"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/api/v1/pipelines/templates/tmpl-1", json={"default_for_kind": "code"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "another template is already the default for 'code'; clear it first"


@pytest.mark.asyncio
async def test_creating_a_second_default_for_a_kind_is_409(app):
    app.state.templates_repo.create = AsyncMock(side_effect=asyncpg.UniqueViolationError("duplicate key"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "Research", "graph_definition": SAMPLE_GRAPH, "default_for_kind": "research"},
        )
    assert resp.status_code == 409, resp.text
    assert "'research'" in resp.json()["detail"]
```

Add `import asyncpg` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/unit/api/v1/test_pipeline_templates_api.py -q -k "default_kind or default_for_a_kind"`
Expected: 4 failed. The null test fails on `assert_awaited_once_with` (the route returned the template without calling `update`); the 409 tests fail with a 500.

- [ ] **Step 3: Change the route**

In `embry0/api/v1/pipeline_templates.py`, add `import asyncpg` beside the other imports and a helper under `_stored_added_skills`:

```python
def _default_kind_taken(kind: str | None) -> HTTPException:
    """The partial unique index on ``default_for_kind`` refused a second holder (RAV-601)."""
    return HTTPException(status_code=409, detail=f"another template is already the default for {kind!r}; clear it first")
```

In `create_template`, wrap the `repo.create(...)` call:

```python
    try:
        template = await repo.create(
            ...existing keyword arguments unchanged...
        )
    except asyncpg.UniqueViolationError:
        raise _default_kind_taken(req.default_for_kind) from None
```

(keep whatever the existing code does with the created template after the call).

In `update_template`, replace the first line of the body:

```python
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    # Only this field's null is a write: it clears the default so another
    # template can take the kind. Every other field's null means "unchanged".
    if "default_for_kind" in req.model_fields_set and req.default_for_kind is None:
        updates["default_for_kind"] = None
```

and wrap the write:

```python
    try:
        updated = await repo.update(template_id, **updates)
    except asyncpg.UniqueViolationError:
        raise _default_kind_taken(updates.get("default_for_kind")) from None
```

In `embry0/api/schemas/__init__.py` line 534, give the update field a description:

```python
    default_for_kind: str | None = Field(
        default=None,
        pattern="^(code|research|analysis|ops)$",
        description="the kind this template is the default route for; an explicit null clears it, an absent field keeps it",
    )
```

- [ ] **Step 4: Run the tests to verify they pass, then the gate**

Run the Step 2 command: 4 passed. Then the backend gate from Global Constraints (the templates test module, `test_route_table.py`, `tests/unit/docs`, `test_user_facing_text.py`; `ruff check`, `ruff format --check`, `mypy embry0`). Read the summary lines.

- [ ] **Step 5: Commit**

```bash
git add embry0/api/v1/pipeline_templates.py embry0/api/schemas/__init__.py tests/unit/api/v1/test_pipeline_templates_api.py
git commit -m "fix(api): a template's default kind clears on null and a second holder is 409

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 3: Deep stats under the Overview tiles (issue P)

**Files:**
- Modify: `frontend/src/lib/types/stats.ts` (two summaries and two optional fields)
- Create: `frontend/src/components/dashboard/DashboardPanel.tsx` (lifted out of `DashboardPage.tsx`, unchanged markup and `data-testid`)
- Create: `frontend/src/components/dashboard/DeepStats.tsx`, `frontend/src/components/dashboard/__tests__/DeepStats.test.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx` (imports `DashboardPanel`, renders `DeepStats` under the two panels), `frontend/src/pages/__tests__/DashboardPage.test.tsx` (one test)

**Interfaces:**
- Consumes: `StatsResponse` (`@/lib/types`), `formatCost`, `formatTokens` (`@/lib/utils`), `sentenceCase` (`@/lib/status`), `Link` from react-router.
- Produces: `DashboardPanel` exported from `@/components/dashboard/DashboardPanel` with the props it has today (`title, viewAll?, isPending, isError, isEmpty, emptyText, children`); `DeepStats({ stats })`.

`GET /stats` (`embry0/api/v1/stats.py:338-346`) carries `queue_depth`, `failure_categories` (a dict with every category, zeros included), `top_expensive_issues` (`trace_id, issue_number, title, repo, cost_usd, job_count`), `cost_by_repo` (`repo, cost_usd, job_count`, top 8), `tokens_by_agent` (`agent_type, runs, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, cache_hit_rate`), `qa_prebuilt_images` (`{active, fresh, stale: [{repo, image_tag}], unknown: [{repo, image_tag}]}` or `null` when the stack has no docker handle) and `credentials` (`{count, quarantined, disabled, env_fallback_keys}` or `null` when the registry is not wired). The Overview test fixture (`DashboardPage.test.tsx`, `ORCH_OK`) has none of the arrays and an empty `failure_categories`: every panel must read that as empty, with no table drawn, so the existing tests keep counting what they count.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/dashboard/__tests__/DeepStats.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { DeepStats } from "../DeepStats";
import type { StatsResponse } from "@/lib/types";

const BASE: StatsResponse = {
  total_issues: 10,
  total_jobs: 20,
  completed: 18,
  failed: 2,
  success_rate: 0.9,
  total_cost_usd: 12.5,
  failure_categories: {},
  daily_cost_usd: 3.45,
  monthly_cost_usd: 47.5,
  queue_depth: 3,
  recent_issues: [],
};

function renderStats(stats: StatsResponse) {
  return render(
    <MemoryRouter>
      <DeepStats stats={stats} />
    </MemoryRouter>,
  );
}

describe("DeepStats", () => {
  it("draws the failure categories as labelled bars, largest first, and leaves out the zeros", () => {
    renderStats({ ...BASE, failure_categories: { sandbox: 1, agent: 4, infra: 0 } });
    const panel = screen.getByTestId("panel-Failures by category");
    const rows = within(panel).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual(["Agent4", "Sandbox1"]);
    expect(within(panel).getByLabelText("Agent")).toHaveAttribute("aria-valuenow", "4");
  });

  it("tables the cost by repository, the tokens by agent and the most expensive issues, each issue linked", () => {
    renderStats({
      ...BASE,
      cost_by_repo: [{ repo: "acme/widgets", cost_usd: 4.5, job_count: 3 }],
      tokens_by_agent: [
        { agent_type: "developer", runs: 5, input_tokens: 1200, output_tokens: 300, cache_read_tokens: 9000, cache_creation_tokens: 0, cost_usd: 2.5, cache_hit_rate: 0.88 },
      ],
      top_expensive_issues: [{ trace_id: "tr-1", issue_number: 42, title: "Fix the widget", repo: "acme/widgets", cost_usd: 3.1, job_count: 2 }],
    });
    expect(within(screen.getByTestId("panel-Cost by repository")).getByText("acme/widgets")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-Tokens by agent")).getByText("Developer")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-Tokens by agent")).getByText("88%")).toBeInTheDocument();
    const issues = screen.getByTestId("panel-Most expensive issues");
    expect(within(issues).getByRole("link", { name: /acme\/widgets#42/ })).toHaveAttribute("href", "/issues/tr-1");
    expect(within(issues).getByText("Fix the widget")).toBeInTheDocument();
  });

  it("says each panel is empty, and draws no table, when a backend sends nothing for it", () => {
    renderStats(BASE);
    expect(screen.queryAllByRole("table")).toHaveLength(0);
    expect(within(screen.getByTestId("panel-Failures by category")).getByText("No failures recorded.")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-Cost by repository")).getByText("No jobs with a cost yet.")).toBeInTheDocument();
  });

  it("reports the queue depth, the QA images and the credentials, and hides the two that are not wired", () => {
    renderStats({
      ...BASE,
      qa_prebuilt_images: { active: 4, fresh: 3, stale: [{ repo: "acme/widgets", image_tag: "t1" }], unknown: [] },
      credentials: { count: 5, quarantined: 1, disabled: 0, env_fallback_keys: ["GITHUB_TOKEN"] },
    });
    const fleet = screen.getByTestId("panel-Fleet");
    expect(within(fleet).getByText("Queue depth")).toBeInTheDocument();
    expect(within(fleet).getByText("3")).toBeInTheDocument();
    expect(within(fleet).getByText("3 fresh · 1 stale · 0 unknown")).toBeInTheDocument();
    expect(within(fleet).getByText("5 · 1 quarantined · 0 disabled · env fallback: GITHUB_TOKEN")).toBeInTheDocument();
    expect(within(fleet).getByTitle("Stale: acme/widgets")).toBeInTheDocument();

    renderStats({ ...BASE, qa_prebuilt_images: null, credentials: null });
    const second = screen.getAllByTestId("panel-Fleet")[1];
    expect(within(second).queryByText("QA images")).toBeNull();
    expect(within(second).queryByText("Credentials")).toBeNull();
  });

  it("spends no gold: the Overview's one gold is the Needs-you tile", () => {
    renderStats({ ...BASE, cost_by_repo: [{ repo: "acme/widgets", cost_usd: 4.5, job_count: 3 }] });
    const gold = [...document.querySelectorAll("*")].filter((el) => /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/.test(el.getAttribute("class") ?? ""));
    expect(gold).toHaveLength(0);
  });
});
```

Add to `frontend/src/pages/__tests__/DashboardPage.test.tsx`:

```tsx
  it("draws the deep stats under the two panels, from the same stats answer", () => {
    mockUseStats.mockReturnValue(
      ready({ ...ORCH_OK.data, cost_by_repo: [{ repo: "acme/widgets", cost_usd: 4.5, job_count: 3 }] }),
    );
    renderPage();
    const panels = screen.getByTestId("panel-Recent issues");
    const deep = screen.getByTestId("panel-Cost by repository");
    expect(panels.compareDocumentPosition(deep) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(deep).getByText("acme/widgets")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/components/dashboard/__tests__/DeepStats.test.tsx src/pages/__tests__/DashboardPage.test.tsx`
Expected: the DeepStats file fails to resolve its module; the Dashboard test fails on `panel-Cost by repository`.

- [ ] **Step 3: Add the types and lift the panel**

Append to `frontend/src/lib/types/stats.ts` before `StatsResponse`:

```ts
export interface QaPrebuiltImageRef {
  repo: string;
  image_tag: string;
}

/** Which repositories' pre-built QA images sit on the current base (#334); null when the stack has no docker handle. */
export interface QaPrebuiltImagesSummary {
  active: number;
  fresh: number;
  stale: QaPrebuiltImageRef[];
  unknown: QaPrebuiltImageRef[];
}

/** The credential registry's size and health; null when the registry is not wired. */
export interface CredentialsSummary {
  count: number;
  quarantined: number;
  disabled: number;
  env_fallback_keys: string[];
}
```

and two fields at the end of `StatsResponse`:

```ts
  qa_prebuilt_images?: QaPrebuiltImagesSummary | null;
  credentials?: CredentialsSummary | null;
```

Move `DashboardPanelProps` and `DashboardPanel` from `DashboardPage.tsx` into `frontend/src/components/dashboard/DashboardPanel.tsx` unchanged, with `import type { ReactNode } from "react"; import { Link } from "react-router";` and `export function DashboardPanel`. `DashboardPage.tsx` imports it from `@/components/dashboard/DashboardPanel`.

- [ ] **Step 4: Write DeepStats**

`frontend/src/components/dashboard/DeepStats.tsx`:

```tsx
import type { ReactNode } from "react";
import { Link } from "react-router";
import { DashboardPanel } from "@/components/dashboard/DashboardPanel";
import { sentenceCase } from "@/lib/status";
import { formatCost, formatTokens } from "@/lib/utils";
import type { StatsResponse } from "@/lib/types";

/** How an expensive issue is named: owner/repo#42 on GitHub, the repository alone elsewhere. */
function issueKey(issue: { repo: string; issue_number: number }): string {
  return issue.issue_number > 0 ? `${issue.repo}#${issue.issue_number}` : issue.repo;
}

function FailureBars({ categories }: { categories: Record<string, number> }) {
  const rows = Object.entries(categories)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const max = rows[0]?.[1] ?? 0;
  return (
    <ul className="space-y-2 px-3 py-3">
      {rows.map(([category, n]) => {
        const label = sentenceCase(category);
        return (
          <li key={category} className="space-y-1 text-sm">
            <div className="flex items-baseline justify-between">
              <span className="text-fg-secondary">{label}</span>
              <span className="tabular-nums text-fg">{n}</span>
            </div>
            <div
              role="meter"
              aria-label={label}
              aria-valuenow={n}
              aria-valuemin={0}
              aria-valuemax={max}
              className="h-1.5 w-full overflow-hidden rounded-full bg-white/5"
            >
              <div className="h-full rounded-full bg-fg-muted" style={{ width: `${max > 0 ? (n / max) * 100 : 0}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Row({ label, value, title }: { label: string; value: ReactNode; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-3 py-1.5 text-sm">
      <dt className="text-fg-secondary">{label}</dt>
      <dd className="text-right tabular-nums text-fg" title={title}>
        {value}
      </dd>
    </div>
  );
}

/**
 * The rest of /stats (spec 3.7, "Deep stats"), under the Overview's tiles and
 * panels: failures by category, cost by repository, tokens by agent, the most
 * expensive issues, and one small list for the fleet (queue depth, pre-built
 * QA images, the credential registry). A field a backend does not send, or
 * sends null, leaves its panel empty or its row out; nothing here is gold.
 */
export function DeepStats({ stats }: { stats: StatsResponse }) {
  const failures = Object.values(stats.failure_categories ?? {}).some((n) => n > 0);
  const costByRepo = stats.cost_by_repo ?? [];
  const tokens = stats.tokens_by_agent ?? [];
  const expensive = stats.top_expensive_issues ?? [];
  const images = stats.qa_prebuilt_images ?? null;
  const credentials = stats.credentials ?? null;

  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2" data-testid="deep-stats">
      <DashboardPanel title="Failures by category" isPending={false} isError={false} isEmpty={!failures} emptyText="No failures recorded.">
        <FailureBars categories={stats.failure_categories} />
      </DashboardPanel>

      <DashboardPanel title="Fleet" isPending={false} isError={false} isEmpty={false} emptyText="">
        <dl className="divide-y divide-border py-1">
          <Row label="Queue depth" value={stats.queue_depth} />
          {images && (
            <Row
              label="QA images"
              value={`${images.fresh} fresh · ${images.stale.length} stale · ${images.unknown.length} unknown`}
              title={images.stale.length > 0 ? `Stale: ${images.stale.map((i) => i.repo).join(", ")}` : undefined}
            />
          )}
          {credentials && (
            <Row
              label="Credentials"
              value={`${credentials.count} · ${credentials.quarantined} quarantined · ${credentials.disabled} disabled${
                credentials.env_fallback_keys.length > 0 ? ` · env fallback: ${credentials.env_fallback_keys.join(", ")}` : ""
              }`}
            />
          )}
        </dl>
      </DashboardPanel>

      <DashboardPanel title="Cost by repository" isPending={false} isError={false} isEmpty={costByRepo.length === 0} emptyText="No jobs with a cost yet.">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col" className="num">Jobs</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {costByRepo.map((row) => (
              <tr key={row.repo}>
                <td className="w-full max-w-0 truncate font-mono text-xs">{row.repo}</td>
                <td className="num tabular-nums text-fg-secondary">{row.job_count}</td>
                <td className="num tabular-nums">{formatCost(row.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DashboardPanel>

      <DashboardPanel title="Tokens by agent" isPending={false} isError={false} isEmpty={tokens.length === 0} emptyText="No agent runs recorded yet.">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col" className="num">Runs</th>
              <th scope="col" className="num hidden sm:table-cell">Input</th>
              <th scope="col" className="num hidden sm:table-cell">Output</th>
              <th scope="col" className="num">Cache hits</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((row) => (
              <tr key={row.agent_type}>
                <td className="w-full">{sentenceCase(row.agent_type)}</td>
                <td className="num tabular-nums text-fg-secondary">{row.runs}</td>
                <td className="num hidden tabular-nums text-fg-secondary sm:table-cell">{formatTokens(row.input_tokens)}</td>
                <td className="num hidden tabular-nums text-fg-secondary sm:table-cell">{formatTokens(row.output_tokens)}</td>
                <td className="num tabular-nums">{`${Math.round(row.cache_hit_rate * 100)}%`}</td>
                <td className="num tabular-nums">{formatCost(row.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DashboardPanel>

      <DashboardPanel
        title="Most expensive issues"
        viewAll={{ to: "/issues", label: "All issues" }}
        isPending={false}
        isError={false}
        isEmpty={expensive.length === 0}
        emptyText="No issues with a cost yet."
      >
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Issue</th>
              <th scope="col" className="num hidden sm:table-cell">Jobs</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {expensive.map((issue) => (
              <tr key={issue.trace_id}>
                <td className="w-full max-w-0">
                  <Link to={`/issues/${issue.trace_id}`} className="block truncate hover:underline" title={issue.title}>
                    <span className="font-mono text-xs text-fg-secondary">{issueKey(issue)}</span>
                    {issue.title && <span className="ml-2">{issue.title}</span>}
                  </Link>
                </td>
                <td className="num hidden tabular-nums text-fg-secondary sm:table-cell">{issue.job_count}</td>
                <td className="num tabular-nums">{formatCost(issue.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DashboardPanel>
    </div>
  );
}
```

The cache hit rate is rounded to a whole percent here on purpose: `formatPercent` in `@/lib/utils` writes one decimal (`88.0%`), which is more than a table cell wants. The test pins `88%`.

In `DashboardPage.tsx`, after the closing `</div>` of the two-panel grid and inside the page's `space-y-5` box:

```tsx
      {stats.data && <DeepStats stats={stats.data} />}
```

with `import { DeepStats } from "@/components/dashboard/DeepStats";`.

- [ ] **Step 5: Run the tests to verify they pass, then the gate**

Run the Step 2 command: both files pass, the existing Dashboard tests included. Then the frontend gate; read the summary lines.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types/stats.ts frontend/src/components/dashboard/DashboardPanel.tsx frontend/src/components/dashboard/DeepStats.tsx frontend/src/components/dashboard/__tests__/DeepStats.test.tsx frontend/src/pages/DashboardPage.tsx frontend/src/pages/__tests__/DashboardPage.test.tsx
git commit -m "feat(dashboard): the rest of /stats under the Overview tiles

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 4: Build a QA image from the dashboard (issue P)

**Files:**
- Modify: `frontend/src/api/qaDashboard.ts` (append `buildQaImage`), `frontend/src/api/__tests__/qaDashboard.test.ts` (append one test)
- Create: `frontend/src/components/qa/BuildImageButton.tsx`, `frontend/src/components/qa/__tests__/BuildImageButton.test.tsx`
- Modify: `frontend/src/pages/QaRepoCachePage.tsx` (the button beside the back link), `frontend/src/pages/QaRepoDetailPage.tsx` (the button beside the title)

**Interfaces:**
- Consumes: `Dialog`, `Switch`, `Input`, `Label`, `Button`, `toastApiError`, `toast`, `useQueryClient`.
- Produces: `buildQaImage(repo, branch, force): Promise<QaImageBuildResult>`; `BuildImageButton({ repo })`.

`POST /qa/images/build` (`embry0/api/v1/qa_images.py`) takes `{repo, branch = "main", force = false}` (`extra="forbid"`), runs the build synchronously for minutes, and answers `{repo, branch, status: "built" | "skipped"}`; it is operator-tier with the repository allowlist, 422 for an unregistered repository on a deployment without a default forge, 502 when the build fails, 503 when the stack is not wired. The client's default timeout is 30 s; `triggerWatcherRun` in `api/watcher.ts` shows the long-timeout pattern.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/__tests__/qaDashboard.test.ts`, following that file's mocking of `../client`:

```ts
  it("builds a QA image with a long timeout and shows a refusal itself", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { repo: "acme/widgets", branch: "main", status: "built" } });
    await expect(buildQaImage("acme/widgets", "main", false)).resolves.toEqual({ repo: "acme/widgets", branch: "main", status: "built" });
    expect(api.post).toHaveBeenCalledWith(
      "/qa/images/build",
      { repo: "acme/widgets", branch: "main", force: false },
      { timeout: 300_000, suppressValidationToast: true },
    );
  });
```

(import `buildQaImage` from `../qaDashboard`; if the file mocks `api` without `post`, add `post: vi.fn()` to the mock.)

`frontend/src/components/qa/__tests__/BuildImageButton.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockBuild = vi.fn();
vi.mock("@/api/qaDashboard", () => ({ buildQaImage: (...a: unknown[]) => mockBuild(...a) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { BuildImageButton } from "../BuildImageButton";

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BuildImageButton repo="acme/widgets" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockBuild.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.info).mockReset();
});

describe("BuildImageButton", () => {
  it("asks for the branch and whether to force, then builds and says so", async () => {
    mockBuild.mockResolvedValue({ repo: "acme/widgets", branch: "release", status: "built" });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    const dialog = screen.getByRole("dialog", { name: "Build QA image" });
    expect(dialog).toHaveTextContent(/can take minutes/i);
    const branch = screen.getByRole("textbox", { name: "Branch" });
    expect(branch).toHaveValue("main");
    expect(branch).toHaveFocus();
    await userEvent.clear(branch);
    await userEvent.type(branch, "release");
    await userEvent.click(screen.getByRole("switch", { name: "Rebuild even if nothing changed" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    await waitFor(() => expect(mockBuild).toHaveBeenCalledWith("acme/widgets", "release", true));
    expect(toast.success).toHaveBeenCalledWith("QA image built for acme/widgets (release)");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("says the image was already current when the build was skipped", async () => {
    mockBuild.mockResolvedValue({ repo: "acme/widgets", branch: "main", status: "skipped" });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith("QA image already current for acme/widgets (main)"));
  });

  it("keeps the dialog open and shows the server's reason when the build is refused", async () => {
    mockBuild.mockRejectedValue({ response: { status: 422, data: { detail: "ERR_REPO_UNREGISTERED: acme/widgets is not registered" } } });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("acme/widgets is not registered");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("refuses an empty branch before asking the server", async () => {
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Branch" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(mockBuild).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Name the branch to build from.");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/api/__tests__/qaDashboard.test.ts src/components/qa/__tests__/BuildImageButton.test.tsx`
Expected: the api test fails on `buildQaImage` being undefined; the component file fails to resolve its module.

- [ ] **Step 3: Write the api call and the button**

Append to `frontend/src/api/qaDashboard.ts`:

```ts
export interface QaImageBuildResult {
  repo: string;
  branch: string;
  /** `skipped` when the active image already matches the branch's lockfile and `force` was off. */
  status: "built" | "skipped";
}

/**
 * Bake the repository's QA cache image. A fresh build clones, installs and
 * commits an image, which takes minutes, so the timeout is ten times the
 * client's default. A 422 (unregistered repository, bad branch) is shown by
 * the caller, inside its form.
 */
export async function buildQaImage(repo: string, branch: string, force: boolean): Promise<QaImageBuildResult> {
  const { data } = await api.post<QaImageBuildResult>(
    "/qa/images/build",
    { repo, branch, force },
    { timeout: 300_000, suppressValidationToast: true },
  );
  return data;
}
```

`frontend/src/components/qa/BuildImageButton.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Hammer } from "lucide-react";
import { toast } from "sonner";
import { buildQaImage } from "@/api/qaDashboard";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Switch } from "@/components/ui/Switch";

function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 422 || response?.status === 403 || response?.status === 409) {
    return formatErrorDetail(response.data?.detail) ?? "The build was refused.";
  }
  return null;
}

/**
 * Bake the repository's pre-built QA image (spec 3.7, "QA image build"): a
 * button that asks for the branch and whether to rebuild an image that is
 * already current, then calls POST /qa/images/build and waits for it. The
 * route answers built or skipped and no image tag, so the toast says which.
 */
export function BuildImageButton({ repo }: { repo: string }) {
  const [open, setOpen] = useState(false);
  const [branch, setBranch] = useState("main");
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const build = useMutation({
    mutationFn: () => buildQaImage(repo, branch.trim(), force),
    onSuccess: (result) => {
      const where = `${result.repo} (${result.branch})`;
      if (result.status === "built") toast.success(`QA image built for ${where}`);
      else toast.info(`QA image already current for ${where}`);
      void qc.invalidateQueries({ queryKey: ["qa-dashboard", "cache-analytics", repo] });
      setOpen(false);
    },
    onError: (e) => {
      const shown = refusal(e);
      if (shown) setError(shown);
      else toastApiError(e, "QA image build failed");
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!branch.trim()) {
      setError("Name the branch to build from.");
      return;
    }
    setError(null);
    build.mutate();
  };

  return (
    <>
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)} data-testid="build-qa-image">
        <Hammer className="mr-2 h-4 w-4" />
        Build QA image
      </Button>
      {open && (
        <Dialog onClose={() => !build.isPending && setOpen(false)} title="Build QA image" dismissOnBackdrop={false} className="max-w-md">
          <form onSubmit={submit} className="space-y-4 p-5">
            <p className="text-sm text-fg-secondary">
              Clones {repo}, installs its dependencies and commits the result as the image QA runs start from. A fresh build can take minutes; this page waits for it.
            </p>
            <div className="space-y-1">
              <Label htmlFor="qa-image-branch">Branch</Label>
              <Input
                id="qa-image-branch"
                value={branch}
                onChange={(e) => {
                  setError(null);
                  setBranch(e.target.value);
                }}
                disabled={build.isPending}
                data-autofocus
              />
            </div>
            <div className="flex items-center gap-3">
              <Switch id="qa-image-force" checked={force} onCheckedChange={setForce} disabled={build.isPending} />
              <Label htmlFor="qa-image-force">Rebuild even if nothing changed</Label>
            </div>
            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={build.isPending}>
                Cancel
              </Button>
              <Button type="submit" disabled={build.isPending}>
                {build.isPending ? "Building… (can take minutes)" : "Build"}
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </>
  );
}
```

If `Switch` with an `id` and a `<Label htmlFor>` does not give the switch its name in the test (`getByRole("switch", { name })`), pass `label="Rebuild even if nothing changed"` to the Switch and drop the Label, the way `CreateJobDialog` does it in plan 3a.

- [ ] **Step 4: Place the button on the two pages**

`frontend/src/pages/QaRepoCachePage.tsx`: the `px-6 pt-4` box becomes `flex flex-wrap items-center justify-between gap-3 px-6 pt-4` with the back link first and `<BuildImageButton repo={repo} />` after it.

`frontend/src/pages/QaRepoDetailPage.tsx`: wrap the `h1` in `<div className="flex flex-wrap items-center justify-between gap-3">` with `<BuildImageButton repo={repo} />` after the heading.

- [ ] **Step 5: Run the tests to verify they pass, then the gate**

Run the Step 2 command: both pass. Then the frontend gate; read the summary lines.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/qaDashboard.ts frontend/src/api/__tests__/qaDashboard.test.ts frontend/src/components/qa/BuildImageButton.tsx frontend/src/components/qa/__tests__/BuildImageButton.test.tsx frontend/src/pages/QaRepoCachePage.tsx frontend/src/pages/QaRepoDetailPage.tsx
git commit -m "feat(dashboard): build a repository's QA image from its QA pages

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 5: The effective environment of a repository (issue P)

**Files:**
- Modify: `frontend/src/api/environment.ts` (append `fetchResolvedRepoEnv`)
- Modify: `frontend/src/hooks/useEnvironments.ts` (append `resolvedRepoEnvQuery`, `useResolvedRepoEnv`)
- Modify: `frontend/src/pages/RepoDetailPage.tsx` (the `appSettings` section), `frontend/src/pages/__tests__/RepoDetailPage.test.tsx` (the `useEnvironments` mock and two tests)

**Interfaces:**
- Consumes: `EnvVar` (`@/lib/types/environment`), `Switch`, `Label`, `Badge`, `Skeleton`, `PageError`, `statusLabel`.
- Produces: `useResolvedRepoEnv(fullRepo, enabled): UseQueryResult<EnvVar[]>`.

`GET /repos/{repo}/environment/resolve` (`embry0/api/v1/environment.py:176`) answers `{variables: EnvVar[]}`: the global variables with the repository's own written over them by key, secrets masked. It carries no layer field, so the page tells a Repo value from a Global one by whether the key is among the repository's own rows, which it already holds (`useRepoEnv`).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/pages/__tests__/RepoDetailPage.test.tsx`, the `@/hooks/useEnvironments` mock becomes:

```tsx
const mockUseRepoEnv = vi.fn();
const mockUseResolvedRepoEnv = vi.fn();
vi.mock("@/hooks/useEnvironments", () => ({
  useRepoEnv: () => mockUseRepoEnv(),
  useResolvedRepoEnv: (repo: string, enabled: boolean) => mockUseResolvedRepoEnv(repo, enabled),
}));
```

and `beforeEach` gains `mockUseResolvedRepoEnv.mockReset().mockReturnValue({ data: undefined, isPending: false, isError: false, refetch: vi.fn() });`. Append two tests, using the file's `renderPage` helper and `DETAIL` fixture:

```tsx
  it("shows the effective environment on request, each value marked Repo or Global", async () => {
    mockUseRepoEnv.mockReturnValue({ data: [{ key: "API_URL", value: "https://repo.example.test", var_type: "config", description: "" }] });
    mockUseResolvedRepoEnv.mockReturnValue({
      data: [
        { key: "API_URL", value: "https://repo.example.test", var_type: "config", description: "" },
        { key: "SHARED_TOKEN", value: "", var_type: "secret", description: "", masked_preview: "SHAR••••7Q2F" },
      ],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();
    await screen.findByRole("heading", { level: 1 });
    expect(mockUseResolvedRepoEnv).toHaveBeenLastCalledWith("acme/widgets", false);
    const toggle = screen.getByRole("switch", { name: "Effective for this repo" });
    await userEvent.click(toggle);
    expect(mockUseResolvedRepoEnv).toHaveBeenLastCalledWith("acme/widgets", true);
    const rows = screen.getAllByTestId("repo-env-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Repo")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Global")).toBeInTheDocument();
    expect(within(rows[1]).getByText("SHAR••••7Q2F")).toBeInTheDocument();
  });

  it("marks every effective variable Global when the repo sets none", async () => {
    mockUseRepoEnv.mockReturnValue({ data: [] });
    mockUseResolvedRepoEnv.mockReturnValue({
      data: [{ key: "SHARED", value: "x", var_type: "config", description: "" }],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText("No repo-scoped environment variables.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Effective for this repo" }));
    expect(screen.queryByText("No repo-scoped environment variables.")).toBeNull();
    expect(within(screen.getByTestId("repo-env-row")).getByText("Global")).toBeInTheDocument();
  });
```

Add `import userEvent from "@testing-library/user-event";` if the file lacks it.

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/pages/__tests__/RepoDetailPage.test.tsx`
Expected: the two new tests fail on the missing switch; every other test in the file still passes.

- [ ] **Step 3: Write the api call, the hook and the section**

Append to `frontend/src/api/environment.ts`:

```ts
/** The variables a sandbox for this repository gets: global ones with the repo's written over them, secrets masked. */
export async function fetchResolvedRepoEnv(fullRepo: string): Promise<EnvVar[]> {
  const { data } = await api.get(`/repos/${fullRepo}/environment/resolve`);
  return data.variables;
}
```

Append to `frontend/src/hooks/useEnvironments.ts` (and add `fetchResolvedRepoEnv` to its import):

```ts
/** The effective environment of a repo; read only while something shows it. */
export function resolvedRepoEnvQuery(fullRepo: string, enabled = true) {
  return {
    queryKey: ["repo-env", fullRepo, "resolved"],
    queryFn: () => fetchResolvedRepoEnv(fullRepo),
    enabled: !!fullRepo && enabled,
  };
}

export function useResolvedRepoEnv(fullRepo: string, enabled = true) {
  return useQuery(resolvedRepoEnvQuery(fullRepo, enabled));
}
```

In `frontend/src/pages/RepoDetailPage.tsx`: import `useResolvedRepoEnv` beside `useRepoEnv`, `Switch` from `@/components/ui/Switch`, `Label` from `@/components/ui/Label`, `Skeleton` from `@/components/ui/PageSkeleton`, and `useState` from react. Beside `const { data: envVars = [] } = useRepoEnv(full);` add:

```tsx
  // The repo's own values by default; the effective set (global values with
  // the repo's written over them) on request, fetched only then.
  const [effective, setEffective] = useState(false);
  const resolved = useResolvedRepoEnv(full, effective);
  const ownKeys = new Set(envVars.map((v) => v.key));
```

Replace the `appSettings` section's body (from the `<div className="flex">` through the closing `)}` of the table) with:

```tsx
          <div className="flex flex-wrap items-center gap-3">
            <Switch id="repo-env-effective" checked={effective} onCheckedChange={setEffective} />
            <Label htmlFor="repo-env-effective">Effective for this repo</Label>
            <span className="text-xs text-fg-muted">Global values with the repo's written over them, secrets masked.</span>
            <Link
              to={`/environments?repo=${encodeURIComponent(full)}`}
              className="ml-auto text-sm text-fg hover:text-primary hover:underline"
              data-testid="repo-env-manage-link"
            >
              Add or change values
            </Link>
          </div>
          {effective && resolved.isError ? (
            <PageError message="Failed to load the effective environment" onRetry={() => void resolved.refetch()} />
          ) : effective && !resolved.data ? (
            <Skeleton className="h-10 w-full" />
          ) : (effective ? resolved.data ?? [] : envVars).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {effective ? "No variables apply to this repository." : "No repo-scoped environment variables."}
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Key</th>
                    <th scope="col">Type</th>
                    {effective && <th scope="col">Source</th>}
                    <th scope="col">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {(effective ? resolved.data ?? [] : envVars).map((v) => (
                    <tr key={v.key} data-testid="repo-env-row">
                      <td className="font-mono text-xs">{v.key}</td>
                      <td className="whitespace-nowrap">
                        <span className="inline-flex items-center gap-1.5">
                          <Badge tone={v.var_type === "secret" ? "warning" : "neutral"}>{statusLabel(v.var_type)}</Badge>
                          {v.scope === "qa" && <Badge tone="neutral">QA</Badge>}
                        </span>
                      </td>
                      {effective && (
                        <td className="whitespace-nowrap">
                          <Badge tone={ownKeys.has(v.key) ? "info" : "neutral"}>{ownKeys.has(v.key) ? "Repo" : "Global"}</Badge>
                        </td>
                      )}
                      <td className="w-full max-w-0 truncate font-mono text-xs text-fg-muted">
                        {v.var_type === "secret" ? v.masked_preview || "****" : v.value}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
```

If `Switch` with `id` and a `<Label htmlFor>` does not give the switch its name in the test, pass `label="Effective for this repo"` to the Switch and render the Label as a plain `<span>`.

- [ ] **Step 4: Run the tests to verify they pass, then the gate**

Run the Step 2 command: the whole file passes. Then the frontend gate; read the summary lines.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/environment.ts frontend/src/hooks/useEnvironments.ts frontend/src/pages/RepoDetailPage.tsx frontend/src/pages/__tests__/RepoDetailPage.test.tsx
git commit -m "feat(dashboard): the effective environment of a repository on its page

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 6: Template badges and actions on the Pipelines page (issue P)

**Files:**
- Modify: `frontend/src/lib/types/pipelines.ts` (`TEMPLATE_KINDS`, `TemplateKind`, three optional summary fields, one template field)
- Modify: `frontend/src/api/pipelines.ts` (`mapSummary` keeps the three fields, `mapTemplate` keeps `default_for_kind`; append `duplicateTemplate`, `setTemplateDefaultKind`), `frontend/src/api/__tests__/pipelines.test.ts` (append)
- Modify: `frontend/src/hooks/usePipelines.ts` (append `useDuplicateTemplate`, `useSetTemplateDefault`)
- Create: `frontend/src/components/pipelines/TemplateActions.tsx`, `frontend/src/components/pipelines/DuplicateTemplateDialog.tsx`, `frontend/src/components/pipelines/__tests__/TemplateActions.test.tsx`
- Modify: `frontend/src/pages/PipelinesPage.tsx` (the card), `frontend/src/pages/__tests__/PipelinesPage.test.tsx` (one regex, the hooks mock, three tests), `frontend/src/pages/__tests__/PipelinesPage.open.test.tsx` (the hooks mock)

**Interfaces:**
- Consumes: Task 2's `PUT` semantics (explicit null clears; 409 names the kind); `Menu` (`@/components/ui/Menu`), `Dialog`, `Badge`, `Input`, `Label`, `Button`, `toastApiError`, `toast`.
- Produces: `PipelineTemplateSummary.is_builtin?`, `.default_for_kind?`, `.sandbox_profile?`; `duplicateTemplate(id, name)`, `setTemplateDefaultKind(id, kind | null)`; `TemplateActions({ template })`.

The list route answers `id, name, description, sandbox_profile, is_builtin, default_for_kind, created_at, updated_at` (`embry0/storage/repositories/pipeline_templates.py:24`); `mapSummary` drops all but four today. `POST /pipelines/templates/{id}/duplicate` takes `{name}` (1 to 100 characters, unique) and answers the new template (201). A card on `/pipelines` is a `<button>`; a `Menu` is a button too, so the menu sits beside the card, not inside it, which is also what keeps `getByRole("button", { name: /^Issue to PR/ })` naming the card alone.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/__tests__/pipelines.test.ts` inside its `describe` (its `api` mock has `get`, `post`, `put`, `delete`):

```ts
  it("fetchTemplates keeps is_builtin, default_for_kind and sandbox_profile", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: [{ id: "abc", name: "quick-fix", description: "", sandbox_profile: "node", is_builtin: true, default_for_kind: "code", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" }],
    });
    const [row] = await fetchTemplates();
    expect(row).toMatchObject({ template_id: "abc", is_builtin: true, default_for_kind: "code", sandbox_profile: "node" });
  });

  it("duplicateTemplate posts the new name and maps the copy", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: { id: "def", name: "quick-fix (copy)", description: "", sandbox_profile: null, graph_definition: SAMPLE_GRAPH, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
    });
    await expect(duplicateTemplate("abc", "quick-fix (copy)")).resolves.toMatchObject({ template_id: "def", name: "quick-fix (copy)" });
    expect(api.post).toHaveBeenCalledWith("/pipelines/templates/abc/duplicate", { name: "quick-fix (copy)" });
  });

  it("setTemplateDefaultKind sends the kind, and an explicit null to clear it", async () => {
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { id: "abc", name: "quick-fix", description: "", sandbox_profile: null, graph_definition: SAMPLE_GRAPH, default_for_kind: null, created_at: "", updated_at: "" },
    });
    await setTemplateDefaultKind("abc", "research");
    expect(api.put).toHaveBeenLastCalledWith("/pipelines/templates/abc", { default_for_kind: "research" });
    await setTemplateDefaultKind("abc", null);
    expect(api.put).toHaveBeenLastCalledWith("/pipelines/templates/abc", { default_for_kind: null });
  });
```

(import `duplicateTemplate` and `setTemplateDefaultKind` from `../pipelines`).

`frontend/src/components/pipelines/__tests__/TemplateActions.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const duplicate = vi.fn();
const setDefault = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useDuplicateTemplate: () => ({ mutate: duplicate, isPending: false }),
  useSetTemplateDefault: () => ({ mutate: setDefault, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { TemplateActions } from "../TemplateActions";

const TEMPLATE = { template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z", is_builtin: false, default_for_kind: null, sandbox_profile: null };

beforeEach(() => {
  duplicate.mockReset();
  setDefault.mockReset();
});

describe("TemplateActions", () => {
  it("offers Duplicate and a default for each kind, naming the one it holds", async () => {
    render(<TemplateActions template={{ ...TEMPLATE, default_for_kind: "code" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    const names = screen.getAllByRole("menuitem").map((i) => i.textContent);
    expect(names).toEqual(["Duplicate…", "Clear default (code)", "Make default for research", "Make default for analysis", "Make default for ops"]);
  });

  it("makes the template the default for a kind", async () => {
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Make default for research" }));
    expect(setDefault).toHaveBeenCalledWith({ templateId: "t1", kind: "research" }, expect.anything());
  });

  it("duplicates through a dialog that proposes a name", async () => {
    duplicate.mockImplementation((_vars: unknown, opts: { onSuccess: (t: unknown) => void }) => opts.onSuccess({ template_id: "t2", name: "Nightly" }));
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate…" }));
    const name = screen.getByRole("textbox", { name: "Name" });
    expect(name).toHaveValue("Issue to PR (copy)");
    expect(name).toHaveFocus();
    await userEvent.clear(name);
    await userEvent.type(name, "Nightly");
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(duplicate).toHaveBeenCalledWith({ templateId: "t1", name: "Nightly" }, expect.anything());
    expect(toast.success).toHaveBeenCalledWith("Duplicated as Nightly");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("refuses an empty name before asking the server", async () => {
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate…" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Name" }));
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(duplicate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Name the copy.");
  });
});
```

In `frontend/src/pages/__tests__/PipelinesPage.test.tsx`: the `@/hooks/usePipelines` mock gains `useDuplicateTemplate: () => ({ mutate: vi.fn(), isPending: false })` and `useSetTemplateDefault: () => ({ mutate: mockSetDefault, isPending: false })` (`const mockSetDefault = vi.fn()` hoisted beside `mockUseTemplates`); the card query in the sentence-case test becomes `screen.getByRole("button", { name: /^Issue to PR/ })`; append:

```tsx
  it("badges a built-in template and the default for a kind", () => {
    mockUseTemplates.mockReturnValue({
      data: [
        { template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z", is_builtin: true, default_for_kind: "code" },
        { template_id: "t2", name: "Nightly", description: "", created_at: "2026-10-01T00:00:00Z", is_builtin: false, default_for_kind: null },
      ],
      isLoading: false,
    });
    renderPage();
    const first = screen.getByRole("button", { name: /^Issue to PR/ });
    expect(first).toHaveTextContent("Built in");
    expect(first).toHaveTextContent("Default for code");
    expect(screen.getByRole("button", { name: /^Nightly/ })).not.toHaveTextContent("Default for");
  });

  it("opens a card's menu without opening the template", async () => {
    mockUseTemplates.mockReturnValue({
      data: [{ template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z" }],
      isLoading: false,
    });
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Make default for ops" }));
    expect(mockSetDefault).toHaveBeenCalledWith({ templateId: "t1", kind: "ops" }, expect.anything());
    expect(screen.queryByTestId("pipeline-editor")).toBeNull();
  });

  it("shows the server's reason when a default is taken", async () => {
    mockSetDefault.mockImplementation((_vars: unknown, opts: { onError: (e: unknown) => void }) =>
      opts.onError({ response: { status: 409, data: { detail: "another template is already the default for 'ops'; clear it first" } } }),
    );
    mockUseTemplates.mockReturnValue({
      data: [{ template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z" }],
      isLoading: false,
    });
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Make default for ops" }));
    expect(mockToastApiError).toHaveBeenCalledWith(expect.objectContaining({ response: expect.objectContaining({ status: 409 }) }), "Could not change the default");
  });
```

with `const mockToastApiError = vi.hoisted(() => vi.fn())` and `vi.mock("@/api/errorToast", () => ({ toastApiError: mockToastApiError }))` at the top of the file (the real `toastApiError` reads the 409 detail into the toast; the test pins that the page hands it the error and the context). Add `import userEvent from "@testing-library/user-event";` to `PipelinesPage.test.tsx` (it uses `fireEvent` today). `PipelinesPage.open.test.tsx` mocks `@/hooks/usePipelines` too (line 50): give that mock the same two hooks, or `TemplateActions` renders against an undefined hook.

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/api/__tests__/pipelines.test.ts src/components/pipelines/__tests__/TemplateActions.test.tsx src/pages/__tests__/PipelinesPage.test.tsx src/pages/__tests__/PipelinesPage.open.test.tsx`
Expected: the api tests fail on missing exports and dropped fields; the TemplateActions file fails to resolve; the three page tests fail on the missing badges and menu.

- [ ] **Step 3: Types, api and hooks**

`frontend/src/lib/types/pipelines.ts`, before `PipelineTemplate`:

```ts
/** The job kinds a template can be the default route for (RAV-601); at most one template per kind. */
export const TEMPLATE_KINDS = ["code", "research", "analysis", "ops"] as const;
export type TemplateKind = (typeof TEMPLATE_KINDS)[number];
```

`PipelineTemplate` gains `default_for_kind?: TemplateKind | null;` and `PipelineTemplateSummary` gains:

```ts
  /** Seeded by embry0; Save As keeps a copy of your own. */
  is_builtin?: boolean;
  /** The kind this template is the default route for, or null. */
  default_for_kind?: TemplateKind | null;
  sandbox_profile?: string | null;
```

`frontend/src/api/pipelines.ts`: `ApiTemplateSummary` gains `default_for_kind?: string | null;`; `mapSummary` returns `is_builtin: row.is_builtin === true, default_for_kind: (row.default_for_kind as TemplateKind | null | undefined) ?? null` and the row's `sandbox_profile` unchanged too; `mapTemplate` adds `default_for_kind: (row.default_for_kind as TemplateKind | null | undefined) ?? null`. Append:

```ts
/** A copy of a template under a new name (its graph and sandbox profile; the description is the copy's own). */
export async function duplicateTemplate(templateId: string, name: string): Promise<PipelineTemplate> {
  const { data } = await api.post<ApiTemplate>(`/pipelines/templates/${templateId}/duplicate`, { name });
  return mapTemplate(data);
}

/**
 * Make a template the default route for a kind, or clear it with null. The
 * null is sent on purpose: the route reads an absent field as "unchanged".
 * A kind another template holds is a 409 the caller shows.
 */
export async function setTemplateDefaultKind(templateId: string, kind: TemplateKind | null): Promise<PipelineTemplate> {
  const { data } = await api.put<ApiTemplate>(`/pipelines/templates/${templateId}`, { default_for_kind: kind });
  return mapTemplate(data);
}
```

(import `TemplateKind` from `@/lib/types`.) Append to `frontend/src/hooks/usePipelines.ts`:

```ts
export function useDuplicateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ templateId, name }: { templateId: string; name: string }) => duplicateTemplate(templateId, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline-templates"] }),
  });
}

/** Set or clear (kind null) a template's default kind; the list and the template are read again. */
export function useSetTemplateDefault() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ templateId, kind }: { templateId: string; kind: TemplateKind | null }) => setTemplateDefaultKind(templateId, kind),
    onSuccess: (_data, { templateId }) => invalidateTemplate(qc, templateId),
  });
}
```

- [ ] **Step 4: The actions, the dialog and the card**

`frontend/src/components/pipelines/DuplicateTemplateDialog.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { useDuplicateTemplate } from "@/hooks/usePipelines";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import type { PipelineTemplateSummary } from "@/lib/types";

/** Name the copy (spec 3.7): POST /pipelines/templates/{id}/duplicate wants a unique name. */
export function DuplicateTemplateDialog({ template, onClose }: { template: PipelineTemplateSummary; onClose: () => void }) {
  const [name, setName] = useState(`${template.name} (copy)`);
  const [error, setError] = useState<string | null>(null);
  const duplicate = useDuplicateTemplate();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Name the copy.");
      return;
    }
    duplicate.mutate(
      { templateId: template.template_id, name: trimmed },
      {
        onSuccess: (copy) => {
          toast.success(`Duplicated as ${copy.name}`);
          onClose();
        },
        onError: (err) => toastApiError(err, "Failed to duplicate"),
      },
    );
  };

  return (
    <Dialog onClose={onClose} title="Duplicate template" className="max-w-md">
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="space-y-1">
          <Label htmlFor="duplicate-template-name">Name</Label>
          <Input
            id="duplicate-template-name"
            value={name}
            onChange={(e) => {
              setError(null);
              setName(e.target.value);
            }}
            maxLength={100}
            disabled={duplicate.isPending}
            data-autofocus
          />
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={duplicate.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={duplicate.isPending}>
            {duplicate.isPending ? "Duplicating…" : "Duplicate"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
```

`frontend/src/components/pipelines/TemplateActions.tsx`:

```tsx
import { useState } from "react";
import { toastApiError } from "@/api/errorToast";
import { useSetTemplateDefault } from "@/hooks/usePipelines";
import { Menu, type MenuItem } from "@/components/ui/Menu";
import { DuplicateTemplateDialog } from "@/components/pipelines/DuplicateTemplateDialog";
import { TEMPLATE_KINDS, type PipelineTemplateSummary } from "@/lib/types";

/**
 * A saved template's menu on the Pipelines page (spec 3.7): duplicate it
 * under a new name, make it the default route for a kind, or clear the kind
 * it holds. It sits beside the card, which is itself a button, so a choice
 * here never opens the template.
 */
export function TemplateActions({ template }: { template: PipelineTemplateSummary }) {
  const [duplicating, setDuplicating] = useState(false);
  const setDefault = useSetTemplateDefault();
  const held = template.default_for_kind ?? null;

  const items: MenuItem[] = [{ label: "Duplicate…", onSelect: () => setDuplicating(true) }];
  if (held) {
    items.push({
      label: `Clear default (${held})`,
      onSelect: () =>
        setDefault.mutate({ templateId: template.template_id, kind: null }, { onError: (e) => toastApiError(e, "Could not clear the default") }),
    });
  }
  for (const kind of TEMPLATE_KINDS) {
    if (kind === held) continue;
    items.push({
      label: `Make default for ${kind}`,
      onSelect: () =>
        setDefault.mutate({ templateId: template.template_id, kind }, { onError: (e) => toastApiError(e, "Could not change the default") }),
    });
  }

  return (
    <>
      <Menu label={`Actions for ${template.name}`} items={items} triggerClassName="h-7 w-7" />
      {duplicating && <DuplicateTemplateDialog template={template} onClose={() => setDuplicating(false)} />}
    </>
  );
}
```

`frontend/src/pages/PipelinesPage.tsx`: `TemplateCard` becomes a `relative` box holding the card button (unchanged classes, the chevron removed, badges under the description) and the menu in its top-right corner:

```tsx
function TemplateCard({ template, onOpen, loading }: TemplateCardProps) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => onOpen(template)}
        disabled={loading}
        className="group w-full text-left rounded-lg border border-border bg-surface hover:bg-overlay hover:border-border-strong p-4 pr-12 transition-colors disabled:opacity-50 disabled:cursor-wait"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="shrink-0 w-8 h-8 rounded-lg bg-overlay border border-border flex items-center justify-center">
            <GitBranch size={14} className="text-fg-secondary" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-white/90 truncate">{template.name}</p>
            {template.description && (
              <p className="text-xs text-white/40 mt-0.5 line-clamp-1">{template.description}</p>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-2xs text-fg-muted">
          <Calendar size={10} />
          <span>{formatDay(template.created_at)}</span>
          {template.is_builtin && <Badge tone="neutral">Built in</Badge>}
          {template.default_for_kind && <Badge tone="info">{`Default for ${template.default_for_kind}`}</Badge>}
        </div>
      </button>
      <div className="absolute right-2 top-2">
        <TemplateActions template={template} />
      </div>
    </div>
  );
}
```

(imports: `Badge` from `@/components/ui/Badge`, `TemplateActions` from `@/components/pipelines/TemplateActions`; drop `ChevronRight`.) The `handleOpen` spread `{ ...template, ...full }` keeps working.

- [ ] **Step 5: Run the tests to verify they pass, then the gate**

Run the Step 2 command: every file passes. Then the frontend gate; read the summary lines.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types/pipelines.ts frontend/src/api/pipelines.ts frontend/src/api/__tests__/pipelines.test.ts frontend/src/hooks/usePipelines.ts frontend/src/components/pipelines frontend/src/pages/PipelinesPage.tsx frontend/src/pages/__tests__/PipelinesPage.test.tsx frontend/src/pages/__tests__/PipelinesPage.open.test.tsx
git commit -m "feat(dashboard): template badges, duplicate and default-for-kind on the Pipelines page

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 7: The Hosts page (issue Q)

**Files:**
- Create: `frontend/src/api/hosts.ts`, `frontend/src/api/__tests__/hosts.test.ts`, `frontend/src/hooks/useHosts.ts`, `frontend/src/lib/hosts.ts`
- Create: `frontend/src/pages/HostsPage.tsx`, `frontend/src/pages/__tests__/HostsPage.test.tsx`
- Create: `frontend/src/components/hosts/HostFormDialog.tsx`, `frontend/src/components/hosts/__tests__/HostFormDialog.test.tsx`
- Modify: `frontend/src/components/layout/navGroups.ts` (Infra gains Hosts after Sandboxes), `frontend/src/components/layout/Breadcrumbs.tsx` (`hosts: "Hosts"`), `frontend/src/components/layout/__tests__/Sidebar.test.tsx` (the three Infra tests), `frontend/src/components/layout/__tests__/Breadcrumbs.test.tsx`, `frontend/src/router/index.tsx`
- Modify: `docs/hosts.md` (one sentence), `docs/glossary.md` (a `### HostsPage` entry after `### MergeApprovalsPage`)

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 1), `Dialog`, `Menu`, `Switch`, `AdminButton`, `useIsAdmin`, `FilterSelect`, `TagInput`, `Select`, `Input`, `Label`, `Badge`, `EmptyState`, `PageError`, `TableSkeleton`, `formatDateTime`, `formatErrorDetail`, `toastApiError`, `toast`.
- Produces: the Hosts page at `/hosts`.

The routes (`embry0/api/v1/hosts.py`, `docs/hosts.md`, `docs/api.md:314`): `GET /hosts?source=manual|tailnet&include_removed=` answers `{hosts, transports, sources, statuses, log_source_kinds}` (any tier); `POST /hosts` (admin, 201; `HostCreate`: `name` matching `^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`-style, `address`, `transport: tailnet|ssh|http`, `roles: string[]`, `log_source: {kind: journald|docker|loki, selector} | null`, `tags: string[]`, `repos: [{repo, role}]`; 409 on a taken name, 422 on an address that does not fit the transport); `GET /hosts/{id}`; `PATCH /hosts/{id}` (admin; every field optional, `log_source: null` clears, `repos` replaces, `status: active|removed`); `DELETE /hosts/{id}` (admin, 204, the row stays `removed`); `POST /hosts/{id}/probe` (operator; `{probe: {reachable, detail, checked_at, last_seen_at, ...}, host}`; 409 on a removed host); `POST /hosts/import/tailnet` (admin; `{confirm: true}`; `{tailnet, credential_id, results: [{name, host_id, outcome, detail}], counts: {created, updated, unchanged, skipped}}`; 502 when the tailnet API fails). A host: `id, name, address, transport, roles, log_source, tags, source, last_seen_at, status, created_at, updated_at, repos`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/api/__tests__/hosts.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));

import { api } from "../client";
import { createHost, fetchHosts, importTailnetHosts, probeHost, removeHost, updateHost } from "../hosts";

beforeEach(() => vi.clearAllMocks());

describe("hosts api", () => {
  it("lists with the route's two filters, sending only the ones set", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { hosts: [], transports: [], sources: [], statuses: [], log_source_kinds: [] } });
    await fetchHosts({ source: undefined, includeRemoved: false });
    expect(api.get).toHaveBeenLastCalledWith("/hosts", { params: {} });
    await fetchHosts({ source: "tailnet", includeRemoved: true });
    expect(api.get).toHaveBeenLastCalledWith("/hosts", { params: { source: "tailnet", include_removed: true } });
  });

  it("writes with the contract's bodies and shows refusals itself", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { id: "host-1" } });
    (api.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { id: "host-1" } });
    (api.delete as ReturnType<typeof vi.fn>).mockResolvedValue({});
    const body = { name: "web-1", address: "100.64.0.10", transport: "tailnet" as const, roles: ["app"], log_source: null, tags: [], repos: [{ repo: "acme/widgets", role: "app" }] };
    await createHost(body);
    expect(api.post).toHaveBeenCalledWith("/hosts", body, { suppressValidationToast: true });
    await updateHost("host-1", { status: "active" });
    expect(api.patch).toHaveBeenCalledWith("/hosts/host-1", { status: "active" }, { suppressValidationToast: true });
    await removeHost("host-1");
    expect(api.delete).toHaveBeenCalledWith("/hosts/host-1");
    await probeHost("host-1");
    expect(api.post).toHaveBeenLastCalledWith("/hosts/host-1/probe", {}, { suppressValidationToast: true });
    await importTailnetHosts();
    expect(api.post).toHaveBeenLastCalledWith("/hosts/import/tailnet", { confirm: true }, { suppressValidationToast: true });
  });
});
```

`frontend/src/components/hosts/__tests__/HostFormDialog.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const create = vi.fn();
const update = vi.fn();
vi.mock("@/hooks/useHosts", () => ({
  useCreateHost: () => ({ mutate: create, isPending: false }),
  useUpdateHost: () => ({ mutate: update, isPending: false }),
}));

import { HostFormDialog } from "../HostFormDialog";

const HOST = {
  id: "host-1", name: "web-1", address: "100.64.0.10", transport: "tailnet" as const, roles: ["app"],
  log_source: { kind: "docker" as const, selector: "widgets-app" }, tags: ["tag:server"], source: "manual" as const,
  last_seen_at: null, status: "active" as const, created_at: "2026-10-16T12:00:00Z", updated_at: "2026-10-16T12:00:00Z",
  repos: [{ repo: "acme/widgets", role: "app" }],
};
const KINDS = ["journald", "docker", "loki"] as const;

beforeEach(() => {
  create.mockReset();
  update.mockReset();
});

describe("HostFormDialog", () => {
  it("creates a host from the form, with the log source and the repositories it names", async () => {
    render(<HostFormDialog logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Add host" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus();
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "web-1");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Transport" }), "ssh");
    await userEvent.type(screen.getByRole("textbox", { name: "Address" }), "web-1.internal:22");
    await userEvent.type(screen.getByRole("textbox", { name: "Roles" }), "app{enter}");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Log source" }), "journald");
    await userEvent.type(screen.getByRole("textbox", { name: "Selector" }), "widgets.service");
    await userEvent.type(screen.getByRole("textbox", { name: "Repositories" }), "acme/widgets{enter}");
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    expect(create).toHaveBeenCalledWith(
      {
        name: "web-1", address: "web-1.internal:22", transport: "ssh", roles: ["app"],
        log_source: { kind: "journald", selector: "widgets.service" }, tags: [], repos: [{ repo: "acme/widgets", role: "app" }],
      },
      expect.anything(),
    );
  });

  it("edits a host with its values filled in and sends the whole editable record", async () => {
    render(<HostFormDialog host={HOST} logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Edit web-1" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Selector" })).toHaveValue("widgets-app");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Log source" }), "");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith(
      { id: "host-1", patch: { name: "web-1", address: "100.64.0.10", transport: "tailnet", roles: ["app"], log_source: null, tags: ["tag:server"], repos: [{ repo: "acme/widgets", role: "app" }] } },
      expect.anything(),
    );
  });

  it("refuses an empty name before asking the server", async () => {
    render(<HostFormDialog logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Name the host.");
  });

  it("keeps the form open and shows the server's refusal", async () => {
    create.mockImplementation((_body: unknown, opts: { onError: (e: unknown) => void }) =>
      opts.onError({ response: { status: 422, data: { detail: "ssh host: expected host or host:port" } } }),
    );
    render(<HostFormDialog logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "web-1");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Transport" }), "ssh");
    await userEvent.type(screen.getByRole("textbox", { name: "Address" }), "ssh://nope");
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("ssh host: expected host or host:port"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Address" })).toHaveValue("ssh://nope");
  });
});
```

`frontend/src/pages/__tests__/HostsPage.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { useAuthStore } from "@/stores/authStore";

const mockUseHosts = vi.fn();
const probe = vi.fn();
const remove = vi.fn();
const update = vi.fn();
const importHosts = vi.fn();
vi.mock("@/hooks/useHosts", () => ({
  useHosts: (filter: unknown) => mockUseHosts(filter),
  useProbeHost: () => ({ mutate: probe, isPending: false }),
  useRemoveHost: () => ({ mutate: remove, isPending: false }),
  useUpdateHost: () => ({ mutate: update, isPending: false }),
  useCreateHost: () => ({ mutate: vi.fn(), isPending: false }),
  useImportTailnetHosts: () => ({ mutate: importHosts, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { HostsPage } from "../HostsPage";

const WEB = {
  id: "host-1", name: "web-1", address: "100.64.0.10", transport: "tailnet", roles: ["app"], log_source: null, tags: ["tag:server"],
  source: "tailnet", last_seen_at: "2026-10-16T11:59:00Z", status: "active", created_at: "2026-10-16T12:00:00Z", updated_at: "2026-10-16T12:00:00Z", repos: [],
};
const DB = { ...WEB, id: "host-2", name: "db-1", address: "100.64.0.11", roles: ["db"], source: "manual", status: "unreachable", last_seen_at: null };
const LIST = { hosts: [WEB, DB], transports: ["tailnet", "ssh", "http"], sources: ["manual", "tailnet"], statuses: ["active", "unreachable", "removed"], log_source_kinds: ["journald", "docker", "loki"] };

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <HostsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  [probe, remove, update, importHosts].forEach((m) => m.mockReset());
  mockUseHosts.mockReset().mockReturnValue(ready(LIST));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("HostsPage", () => {
  it("lists each host with its address, transport, roles, status and last-seen time", () => {
    renderPage();
    const rows = screen.getAllByTestId("host-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("web-1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("100.64.0.10")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Active")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Unreachable")).toBeInTheDocument();
    expect(within(rows[1]).getByText("never")).toBeInTheDocument();
  });

  it("filters by source and shows removed hosts on request, through the hook", async () => {
    renderPage();
    expect(mockUseHosts).toHaveBeenLastCalledWith({ source: undefined, includeRemoved: false });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sources" }), "manual");
    expect(mockUseHosts).toHaveBeenLastCalledWith({ source: "manual", includeRemoved: false });
    await userEvent.click(screen.getByRole("switch", { name: "Show removed" }));
    expect(mockUseHosts).toHaveBeenLastCalledWith({ source: "manual", includeRemoved: true });
  });

  it("probes a host from its menu and says what came back", async () => {
    probe.mockImplementation((_id: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ probe: { reachable: false, detail: "connect timed out" }, host: DB }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for db-1" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Probe" }));
    expect(probe).toHaveBeenCalledWith("host-2", expect.anything());
    expect(toast.error).toHaveBeenCalledWith("db-1 is unreachable: connect timed out");
  });

  it("removes a host behind a confirm", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for web-1" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", { name: "Remove web-1?" });
    expect(dialog).toHaveTextContent(/repository links are dropped/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(remove).toHaveBeenCalledWith("host-1", expect.anything());
  });

  it("restores a removed host from its menu", async () => {
    mockUseHosts.mockReturnValue(ready({ ...LIST, hosts: [{ ...WEB, status: "removed" }] }));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for web-1" }));
    expect(screen.getByRole("menuitem", { name: "Probe" })).toBeDisabled();
    await userEvent.click(screen.getByRole("menuitem", { name: "Restore" }));
    expect(update).toHaveBeenCalledWith({ id: "host-1", patch: { status: "active" } }, expect.anything());
  });

  it("imports from the tailnet behind a confirm and reports the counts", async () => {
    importHosts.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ tailnet: "example.ts.net", credential_id: null, results: [], counts: { created: 1, updated: 2, unchanged: 3, skipped: 0 } }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Import from tailnet" }));
    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(importHosts).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith("Imported from example.ts.net: 1 created, 2 updated, 3 unchanged, 0 skipped");
  });

  it("opens the add form from the page's one gold button", async () => {
    renderPage();
    const add = screen.getByRole("button", { name: "Add host" });
    expect(add.className).toContain("bg-primary");
    await userEvent.click(add);
    expect(screen.getByRole("dialog", { name: "Add host" })).toBeInTheDocument();
  });

  it("offers the admin actions disabled to an operator session", async () => {
    useAuthStore.setState({ authMode: "session", user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    renderPage();
    expect(screen.getByRole("button", { name: "Add host" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import from tailnet" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Actions for web-1" }));
    expect(screen.getByRole("menuitem", { name: "Probe" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Remove" })).toBeDisabled();
  });

  it("shows honest empty and error states", () => {
    mockUseHosts.mockReturnValue(ready({ ...LIST, hosts: [] }));
    renderPage();
    expect(screen.getByText("No hosts yet")).toBeInTheDocument();
    mockUseHosts.mockReturnValue({ data: undefined, isPending: false, isError: true, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText("Failed to load hosts")).toBeInTheDocument();
  });
});
```

(`Menu` renders a disabled item as a disabled button, so `toBeDisabled()` is the assertion.)

In `Sidebar.test.tsx`, the three Infra tests' expected arrays gain `"Hosts"` right after `"Sandboxes"` (titles updated to say so). In `Breadcrumbs.test.tsx`:

```tsx
  it("names the Infra section on the hosts page", () => {
    renderAt("/hosts");
    expect(trail()).toBe("Infra");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/api/__tests__/hosts.test.ts src/components/hosts/__tests__/HostFormDialog.test.tsx src/pages/__tests__/HostsPage.test.tsx src/components/layout/__tests__/Sidebar.test.tsx src/components/layout/__tests__/Breadcrumbs.test.tsx`
Expected: the three new files fail to resolve their modules; the Infra tests fail on the array; the breadcrumb test fails on `embry0`.

- [ ] **Step 3: The api module, the hooks and the labels**

`frontend/src/api/hosts.ts`:

```ts
import { api } from "./client";

export type HostTransport = "tailnet" | "ssh" | "http";
export type HostSource = "manual" | "tailnet";
export type HostStatus = "active" | "unreachable" | "removed";
export type LogSourceKind = "journald" | "docker" | "loki";

export interface HostLogSource {
  kind: LogSourceKind;
  /** A systemd unit for journald, a container name for docker, a LogQL selector for loki. */
  selector: string;
}

export interface HostRepoLink {
  repo: string;
  role: string;
}

/** A machine that runs code embry0 maintains (ADR 0013, docs/hosts.md). */
export interface Host {
  id: string;
  name: string;
  address: string;
  transport: HostTransport;
  roles: string[];
  log_source: HostLogSource | null;
  tags: string[];
  source: HostSource;
  last_seen_at: string | null;
  status: HostStatus;
  created_at: string | null;
  updated_at: string | null;
  repos: HostRepoLink[];
}

export interface HostList {
  hosts: Host[];
  transports: HostTransport[];
  sources: HostSource[];
  statuses: HostStatus[];
  log_source_kinds: LogSourceKind[];
}

/** The editable record: POST /hosts takes all of it, PATCH /hosts/{id} any part. */
export interface HostWrite {
  name: string;
  address: string;
  transport: HostTransport;
  roles: string[];
  log_source: HostLogSource | null;
  tags: string[];
  repos: HostRepoLink[];
}

export type HostPatch = Partial<HostWrite> & { status?: "active" | "removed" };

export interface HostProbe {
  host_id: string;
  transport: HostTransport;
  reachable: boolean;
  detail: string;
  checked_at: string;
  last_seen_at: string | null;
  device: Record<string, unknown> | null;
}

export interface TailnetImportResult {
  tailnet: string;
  credential_id: string | null;
  results: { name: string; host_id: string | null; outcome: string; detail: string }[];
  counts: { created: number; updated: number; unchanged: number; skipped: number };
}

export interface HostsFilter {
  source: HostSource | undefined;
  includeRemoved: boolean;
}

export async function fetchHosts(filter: HostsFilter): Promise<HostList> {
  const params: Record<string, string | boolean> = {};
  if (filter.source) params.source = filter.source;
  if (filter.includeRemoved) params.include_removed = true;
  const { data } = await api.get<HostList>("/hosts", { params });
  return data;
}

export async function fetchHost(hostId: string): Promise<Host> {
  const { data } = await api.get<Host>(`/hosts/${encodeURIComponent(hostId)}`);
  return data;
}

/** Add a host (admin). A taken name is a 409 and a misfit address a 422, both shown in the form. */
export async function createHost(body: HostWrite): Promise<Host> {
  const { data } = await api.post<Host>("/hosts", body, { suppressValidationToast: true });
  return data;
}

export async function updateHost(hostId: string, patch: HostPatch): Promise<Host> {
  const { data } = await api.patch<Host>(`/hosts/${encodeURIComponent(hostId)}`, patch, { suppressValidationToast: true });
  return data;
}

/** Mark the host removed; the row stays so a tailnet import does not bring it back. */
export async function removeHost(hostId: string): Promise<void> {
  await api.delete(`/hosts/${encodeURIComponent(hostId)}`);
}

/** Is the host reachable over its transport? Writes its status and last-seen time. Operator-tier. */
export async function probeHost(hostId: string): Promise<{ probe: HostProbe; host: Host }> {
  const { data } = await api.post<{ probe: HostProbe; host: Host }>(`/hosts/${encodeURIComponent(hostId)}/probe`, {}, { suppressValidationToast: true });
  return data;
}

/** One host per device of the tailnet, created or updated by name (admin). */
export async function importTailnetHosts(): Promise<TailnetImportResult> {
  const { data } = await api.post<TailnetImportResult>("/hosts/import/tailnet", { confirm: true }, { suppressValidationToast: true });
  return data;
}
```

`frontend/src/hooks/useHosts.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createHost,
  fetchHost,
  fetchHosts,
  importTailnetHosts,
  probeHost,
  removeHost,
  updateHost,
  type HostPatch,
  type HostWrite,
  type HostsFilter,
} from "@/api/hosts";

export function useHosts(filter: HostsFilter) {
  return useQuery({ queryKey: ["hosts", filter.source ?? "all", filter.includeRemoved], queryFn: () => fetchHosts(filter) });
}

export function useHost(hostId: string | null) {
  return useQuery({ queryKey: ["hosts", "one", hostId], queryFn: () => fetchHost(hostId!), enabled: !!hostId });
}

function invalidateHosts(qc: ReturnType<typeof useQueryClient>) {
  return qc.invalidateQueries({ queryKey: ["hosts"] });
}

export function useCreateHost() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: HostWrite) => createHost(body), onSuccess: () => invalidateHosts(qc) });
}

export function useUpdateHost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: HostPatch }) => updateHost(id, patch),
    onSuccess: () => invalidateHosts(qc),
  });
}

export function useRemoveHost() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => removeHost(id), onSuccess: () => invalidateHosts(qc) });
}

/** A probe writes the host's status, so the list is read again whatever it found. */
export function useProbeHost() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => probeHost(id), onSettled: () => invalidateHosts(qc) });
}

export function useImportTailnetHosts() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => importTailnetHosts(), onSettled: () => invalidateHosts(qc) });
}
```

`frontend/src/lib/hosts.ts`:

```ts
import type { BadgeTone } from "@/components/ui/Badge";
import type { HostStatus, HostTransport } from "@/api/hosts";

export const HOST_STATUS_TONES: Readonly<Record<HostStatus, BadgeTone>> = {
  active: "success",
  unreachable: "warning",
  removed: "neutral",
};

/** What the address field asks for, per transport, in the words the server checks it against. */
export const ADDRESS_HINTS: Readonly<Record<HostTransport, string>> = {
  tailnet: "An address or MagicDNS name; empty matches the device by name.",
  ssh: "host or host:port",
  http: "host, host:port or a URL",
};
```

- [ ] **Step 4: The form dialog**

`frontend/src/components/hosts/HostFormDialog.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import type { Host, HostTransport, HostWrite, LogSourceKind } from "@/api/hosts";
import { useCreateHost, useUpdateHost } from "@/hooks/useHosts";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { TagInput } from "@/components/ui/TagInput";
import { ADDRESS_HINTS } from "@/lib/hosts";

const TRANSPORTS: HostTransport[] = ["tailnet", "ssh", "http"];

function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Admin scope required.";
  if (response?.status === 409 || response?.status === 422) return formatErrorDetail(response.data?.detail) ?? "The host was not saved.";
  return null;
}

interface Props {
  /** The host to edit; absent, the form adds one. */
  host?: Host;
  logSourceKinds: LogSourceKind[];
  onClose: () => void;
}

/**
 * Add or edit a host (spec 3.7, "Hosts"): the editable record of
 * docs/hosts.md as one form. The server owns the address rules per transport
 * and the name's uniqueness; its 409 and 422 are shown under the form, which
 * stays open with what was typed.
 */
export function HostFormDialog({ host, logSourceKinds, onClose }: Props) {
  const [name, setName] = useState(host?.name ?? "");
  const [transport, setTransport] = useState<HostTransport>(host?.transport ?? "tailnet");
  const [address, setAddress] = useState(host?.address ?? "");
  const [roles, setRoles] = useState<string[]>(host?.roles ?? []);
  const [tags, setTags] = useState<string[]>(host?.tags ?? []);
  const [logKind, setLogKind] = useState<LogSourceKind | "">(host?.log_source?.kind ?? "");
  const [selector, setSelector] = useState(host?.log_source?.selector ?? "");
  const [repos, setRepos] = useState<string[]>(host?.repos.map((r) => r.repo) ?? []);
  const [error, setError] = useState<string | null>(null);
  const create = useCreateHost();
  const update = useUpdateHost();
  const pending = create.isPending || update.isPending;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name the host.");
      return;
    }
    if (logKind && !selector.trim()) {
      setError("Say what to read from the log source.");
      return;
    }
    setError(null);
    const body: HostWrite = {
      name: name.trim(),
      address: address.trim(),
      transport,
      roles,
      log_source: logKind ? { kind: logKind, selector: selector.trim() } : null,
      tags,
      repos: repos.map((repo) => ({ repo, role: "app" })),
    };
    const opts = {
      onSuccess: onClose,
      onError: (err: unknown) => {
        const shown = refusal(err);
        if (shown) setError(shown);
        else toastApiError(err, host ? "The host was not saved" : "The host was not added");
      },
    };
    if (host) update.mutate({ id: host.id, patch: body }, opts);
    else create.mutate(body, opts);
  };

  return (
    <Dialog onClose={() => !pending && onClose()} title={host ? `Edit ${host.name}` : "Add host"} dismissOnBackdrop={false} className="max-w-xl">
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="host-name">Name</Label>
            <Input id="host-name" value={name} onChange={(e) => setName(e.target.value)} disabled={pending} data-autofocus />
            <p className="text-xs text-fg-muted">Letters, digits, dots, underscores and dashes; unique.</p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="host-transport">Transport</Label>
            <Select id="host-transport" value={transport} onChange={(e) => setTransport(e.target.value as HostTransport)} disabled={pending}>
              {TRANSPORTS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="host-address">Address</Label>
          <Input id="host-address" value={address} onChange={(e) => setAddress(e.target.value)} disabled={pending} />
          <p className="text-xs text-fg-muted">{ADDRESS_HINTS[transport]}</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="host-roles">Roles</Label>
            <TagInput id="host-roles" value={roles} onChange={setRoles} placeholder="app, db, worker…" disabled={pending} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="host-tags">Tags</Label>
            <TagInput id="host-tags" value={tags} onChange={setTags} placeholder="Add a tag…" disabled={pending} />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="host-log-kind">Log source</Label>
            <Select id="host-log-kind" value={logKind} onChange={(e) => setLogKind(e.target.value as LogSourceKind | "")} disabled={pending}>
              <option value="">None</option>
              {logSourceKinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </Select>
          </div>
          {logKind && (
            <div className="space-y-1">
              <Label htmlFor="host-log-selector">Selector</Label>
              <Input id="host-log-selector" value={selector} onChange={(e) => setSelector(e.target.value)} disabled={pending} />
              <p className="text-xs text-fg-muted">
                {logKind === "journald" ? "A systemd unit." : logKind === "docker" ? "A container name." : "A LogQL selector."}
              </p>
            </div>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="host-repos">Repositories</Label>
          <TagInput id="host-repos" value={repos} onChange={setRepos} placeholder="owner/name…" disabled={pending} />
          <p className="text-xs text-fg-muted">The repositories this host runs, each linked with the role app.</p>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : host ? "Save" : "Add host"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
```

If `TagInput`'s text box does not take its name from a `<Label htmlFor={id}>` in the test (`getByRole("textbox", { name: "Roles" })`), read `frontend/src/components/ui/TagInput.tsx` and give the box an `aria-label` through the nearest prop it offers; the three labels are "Roles", "Tags" and "Repositories".

- [ ] **Step 5: The page, the nav, the router and the docs**

`frontend/src/pages/HostsPage.tsx`:

```tsx
import { useState } from "react";
import { Server } from "lucide-react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { Host, HostSource } from "@/api/hosts";
import { useHosts, useImportTailnetHosts, useProbeHost, useRemoveHost, useUpdateHost } from "@/hooks/useHosts";
import { AdminButton } from "@/components/auth/AdminButton";
import { HostFormDialog } from "@/components/hosts/HostFormDialog";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Menu } from "@/components/ui/Menu";
import { Switch } from "@/components/ui/Switch";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { HOST_STATUS_TONES } from "@/lib/hosts";
import { sentenceCase, statusLabel } from "@/lib/status";
import { useIsAdmin } from "@/stores/authStore";
import { formatDateTime } from "@/lib/utils";

type Open = { kind: "add" } | { kind: "edit"; host: Host } | { kind: "remove"; host: Host } | { kind: "import" } | null;

/**
 * Hosts (spec 3.7, issue Q): the machines that run the code embry0
 * maintains, from docs/hosts.md. Reads are open to every tier; probing is an
 * operator's; adding, editing, removing and the tailnet import are an admin's
 * and are offered disabled below that. The page's one gold is Add host.
 */
export function HostsPage() {
  const [source, setSource] = useState<HostSource | undefined>(undefined);
  const [includeRemoved, setIncludeRemoved] = useState(false);
  const [open, setOpen] = useState<Open>(null);
  const isAdmin = useIsAdmin();
  const list = useHosts({ source, includeRemoved });
  const probe = useProbeHost();
  const remove = useRemoveHost();
  const update = useUpdateHost();
  const importHosts = useImportTailnetHosts();

  const runProbe = (host: Host) =>
    probe.mutate(host.id, {
      onSuccess: ({ probe: result }) => {
        if (result.reachable) toast.success(`${host.name} is reachable: ${result.detail}`);
        else toast.error(`${host.name} is unreachable: ${result.detail}`);
      },
      onError: (e) => toastApiError(e, `Could not probe ${host.name}`),
    });

  const runImport = () =>
    importHosts.mutate(undefined, {
      onSuccess: (result) => {
        const c = result.counts;
        toast.success(`Imported from ${result.tailnet}: ${c.created} created, ${c.updated} updated, ${c.unchanged} unchanged, ${c.skipped} skipped`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, "Tailnet import failed");
      },
    });

  const runRemove = (host: Host) =>
    remove.mutate(host.id, {
      onSuccess: () => {
        toast.success(`Removed ${host.name}`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, `Could not remove ${host.name}`);
      },
    });

  const restore = (host: Host) =>
    update.mutate({ id: host.id, patch: { status: "active" } }, { onError: (e) => toastApiError(e, `Could not restore ${host.name}`) });

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load hosts" onRetry={() => void list.refetch()} />;
  } else if (list.isPending || !list.data) {
    body = <TableSkeleton />;
  } else if (list.data.hosts.length === 0) {
    body = (
      <EmptyState
        icon={Server}
        title="No hosts yet"
        description="Add the machines that run your repositories' code by hand, or import them from a tailnet."
      />
    );
  } else {
    body = (
      // On a phone the transport, the roles, the source and the last-seen time step aside.
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Address</th>
              <th scope="col" className="hidden sm:table-cell">Transport</th>
              <th scope="col" className="hidden sm:table-cell">Roles</th>
              <th scope="col">Status</th>
              <th scope="col" className="hidden sm:table-cell">Source</th>
              <th scope="col" className="num hidden sm:table-cell">Last seen</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.hosts.map((host) => {
              const removed = host.status === "removed";
              return (
                <tr key={host.id} data-testid="host-row">
                  <td className="whitespace-nowrap font-medium">{host.name}</td>
                  <td className="w-full max-w-0 truncate font-mono text-xs text-fg-secondary">{host.address || "—"}</td>
                  <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{host.transport}</td>
                  <td className="hidden sm:table-cell">
                    <span className="inline-flex flex-wrap gap-1">
                      {host.roles.map((role) => (
                        <Badge key={role} tone="neutral">
                          {role}
                        </Badge>
                      ))}
                    </span>
                  </td>
                  <td className="whitespace-nowrap">
                    <Badge tone={HOST_STATUS_TONES[host.status]}>{statusLabel(host.status)}</Badge>
                  </td>
                  <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{sentenceCase(host.source)}</td>
                  <td className="num hidden whitespace-nowrap text-fg-muted sm:table-cell">
                    {host.last_seen_at ? formatDateTime(host.last_seen_at) : "never"}
                  </td>
                  <td className="w-px">
                    <Menu
                      label={`Actions for ${host.name}`}
                      items={[
                        { label: "Probe", onSelect: () => runProbe(host), disabled: removed || probe.isPending },
                        { label: "Edit", onSelect: () => setOpen({ kind: "edit", host }), disabled: !isAdmin },
                        removed
                          ? { label: "Restore", onSelect: () => restore(host), disabled: !isAdmin }
                          : { label: "Remove", onSelect: () => setOpen({ kind: "remove", host }), destructive: true, disabled: !isAdmin },
                      ]}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-pixel text-xl font-bold">Hosts</h1>
          <p className="text-sm text-muted-foreground">The machines that run the code embry0 maintains, and how it reaches them.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AdminButton type="button" variant="outline" onClick={() => setOpen({ kind: "import" })} disabled={importHosts.isPending}>
            Import from tailnet
          </AdminButton>
          <AdminButton type="button" onClick={() => setOpen({ kind: "add" })}>
            Add host
          </AdminButton>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <FilterSelect
          label="Sources"
          value={source}
          options={list.data?.sources ?? ["manual", "tailnet"]}
          onChange={(v) => setSource(v as HostSource | undefined)}
          optionLabel={sentenceCase}
        />
        <div className="flex items-center gap-2">
          <Switch id="hosts-show-removed" checked={includeRemoved} onCheckedChange={setIncludeRemoved} label="Show removed" />
          <span className="text-sm text-fg-secondary" aria-hidden="true">Show removed</span>
        </div>
      </div>
      {body}
      {(open?.kind === "add" || open?.kind === "edit") && (
        <HostFormDialog
          host={open.kind === "edit" ? open.host : undefined}
          logSourceKinds={list.data?.log_source_kinds ?? ["journald", "docker", "loki"]}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "remove" && (
        <ConfirmDialog
          title={`Remove ${open.host.name}?`}
          confirmLabel="Remove"
          destructive
          pending={remove.isPending}
          onConfirm={() => runRemove(open.host)}
          onClose={() => setOpen(null)}
        >
          Its repository links are dropped. The host stays as removed, so a tailnet import does not bring it back; Restore brings it back.
        </ConfirmDialog>
      )}
      {open?.kind === "import" && (
        <ConfirmDialog
          title="Import hosts from the tailnet?"
          confirmLabel="Import"
          pending={importHosts.isPending}
          onConfirm={runImport}
          onClose={() => setOpen(null)}
        >
          Creates one host per device of the tailnet and updates the ones already here by name. Removed hosts stay removed. Needs a tailnet API key in the credential registry.
        </ConfirmDialog>
      )}
    </div>
  );
}
```

`frontend/src/components/layout/navGroups.ts`: import `Server` from lucide and add `{ path: "/hosts", label: "Hosts", icon: Server },` right after the Sandboxes entry. `Breadcrumbs.tsx`: `hosts: "Hosts",` in `breadcrumbMap`. `router/index.tsx`: `const HostsPage = lazy(() => import("../pages/HostsPage").then(m => ({ default: m.HostsPage })));` and `{ path: "hosts", element: <ErrorBoundary inline><Suspense fallback={fallback}><HostsPage /></Suspense></ErrorBoundary> },` after the `sandboxes/:name` route.

`docs/hosts.md`, at the end of the first section (after "the record is what later work builds on."): `The dashboard's **Hosts** page (Infra) lists them with the same filters, and adds, edits, probes, removes and imports them through these routes.`

`docs/glossary.md`, after `### MergeApprovalsPage`:

```markdown
### HostsPage
The dashboard page at `/hosts` (Infra) over the [host](#host) routes: the list with its source filter and a Show-removed switch, a probe from each row's menu (operator), and for an admin the add and edit form (`HostFormDialog`), Remove and Restore, and the tailnet import, the last two behind a `ConfirmDialog`. See [hosts.md](hosts.md).
```

- [ ] **Step 6: Run the tests to verify they pass, then the gate**

Run the Step 2 command: every file passes. Then the frontend gate; read the summary lines.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/hosts.ts frontend/src/api/__tests__/hosts.test.ts frontend/src/hooks/useHosts.ts frontend/src/lib/hosts.ts frontend/src/pages/HostsPage.tsx frontend/src/pages/__tests__/HostsPage.test.tsx frontend/src/components/hosts frontend/src/components/layout/navGroups.ts frontend/src/components/layout/Breadcrumbs.tsx frontend/src/components/layout/__tests__/Sidebar.test.tsx frontend/src/components/layout/__tests__/Breadcrumbs.test.tsx frontend/src/router/index.tsx docs/hosts.md docs/glossary.md
git commit -m "feat(dashboard): the Hosts page over the host routes

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 8: The Stacks tab on the Watcher page (issue P)

**Files:**
- Modify: `frontend/src/lib/types/watcher.ts` (append the stack types)
- Modify: `frontend/src/api/watcher.ts` (append three calls); Create: `frontend/src/api/__tests__/watcher.test.ts`
- Create: `frontend/src/hooks/useWatcherStacks.ts`, `frontend/src/lib/watcherTabs.ts`
- Create: `frontend/src/components/watcher/WatcherStacksTab.tsx`, `frontend/src/components/watcher/StackEditorDialog.tsx`, `frontend/src/components/watcher/__tests__/WatcherStacksTab.test.tsx`
- Modify: `frontend/src/pages/WatcherPage.tsx` (tabs), `frontend/src/pages/__tests__/WatcherPage.test.tsx` (the `useWatcherStacks` mock and one test)

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 1), `Dialog`, `Menu`, `Switch`, `Tabs`/`TabsList`/`TabsTrigger`/`TabsContent` (`@/components/ui/Tabs`), `AdminButton`, `useIsAdmin`, `Select`, `Input`, `Label`, `Textarea`, `Badge`, `EmptyState`, `PageError`, `TableSkeleton`, `useSearchParams`.
- Produces: `useWatcherStacks()`, `useSaveWatcherStack()`, `useDeleteWatcherStack()`; `WATCHER_TABS`, `watcherTab(value)`.

The routes (`embry0/api/v1/watcher_stacks.py`): `GET /watcher/stacks?enabled_only=` answers `{stacks: [{repo, logql, tracker, tracker_target, settings, enabled}], overridable_settings: string[]}` (operator-tier); `PUT /watcher/stacks/{owner}/{name}` (admin) takes `{logql (required), tracker: linear|github|gitlab (default linear), tracker_target: {} (linear: team_id, project_id), settings: {} (overrides only), enabled: true}` and answers the stored stack plus `ignored_settings` (the unknown keys it dropped); `DELETE /watcher/stacks/{owner}/{name}` (admin) answers `{repo, deleted: true}`, 404 for an unknown one. The Settings page (`SettingsPage.tsx`, `lib/settingsTabs.ts`) shows the `?tab=` pattern.

- [ ] **Step 1: Write the failing tests**

`frontend/src/api/__tests__/watcher.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

import { api } from "../client";
import { deleteWatcherStack, fetchWatcherStacks, putWatcherStack } from "../watcher";

beforeEach(() => vi.clearAllMocks());

describe("watcher stacks api", () => {
  it("lists the stacks with the overridable settings", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { stacks: [], overridable_settings: ["interval_seconds"] } });
    await expect(fetchWatcherStacks()).resolves.toEqual({ stacks: [], overridable_settings: ["interval_seconds"] });
    expect(api.get).toHaveBeenCalledWith("/watcher/stacks");
  });

  it("writes and deletes at the owner/name path and shows refusals itself", async () => {
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { repo: "acme/widgets", ignored_settings: [] } });
    const body = { logql: '{app="widgets"}', tracker: "github" as const, tracker_target: {}, settings: {}, enabled: true };
    await putWatcherStack("acme/widgets", body);
    expect(api.put).toHaveBeenCalledWith("/watcher/stacks/acme/widgets", body, { suppressValidationToast: true });
    (api.delete as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { repo: "acme/widgets", deleted: true } });
    await deleteWatcherStack("acme/widgets");
    expect(api.delete).toHaveBeenCalledWith("/watcher/stacks/acme/widgets");
  });
});
```

`frontend/src/components/watcher/__tests__/WatcherStacksTab.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useAuthStore } from "@/stores/authStore";

const mockUseWatcherStacks = vi.fn();
const save = vi.fn();
const del = vi.fn();
vi.mock("@/hooks/useWatcherStacks", () => ({
  useWatcherStacks: () => mockUseWatcherStacks(),
  useSaveWatcherStack: () => ({ mutate: save, isPending: false }),
  useDeleteWatcherStack: () => ({ mutate: del, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { WatcherStacksTab } from "../WatcherStacksTab";

const STACK = {
  repo: "acme/widgets",
  logql: '{app="widgets"}',
  tracker: "linear",
  tracker_target: { team_id: "team-1", project_id: "proj-1" },
  settings: { interval_seconds: 600 },
  enabled: true,
};
const LIST = { stacks: [STACK], overridable_settings: ["interval_seconds", "min_log_lines"] };

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

beforeEach(() => {
  save.mockReset();
  del.mockReset();
  mockUseWatcherStacks.mockReset().mockReturnValue(ready(LIST));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("WatcherStacksTab", () => {
  it("lists each stack with its tracker, its state and how many settings it overrides", () => {
    render(<WatcherStacksTab />);
    const row = screen.getByTestId("watcher-stack-row");
    expect(within(row).getByText("acme/widgets")).toBeInTheDocument();
    expect(within(row).getByText("Linear")).toBeInTheDocument();
    expect(within(row).getByText("Enabled")).toBeInTheDocument();
    expect(within(row).getByText("1 override")).toBeInTheDocument();
  });

  it("adds a stack through the editor, sending the contract's body", async () => {
    save.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) => opts.onSuccess({ ...STACK, repo: "acme/gadgets", ignored_settings: [] }));
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Add stack" }));
    expect(screen.getByRole("dialog", { name: "Add stack" })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Repository" }), "acme/gadgets");
    await userEvent.type(screen.getByRole("textbox", { name: "LogQL selector" }), '{{app="gadgets"}');
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Tracker" }), "github");
    await userEvent.clear(screen.getByRole("textbox", { name: "Settings overrides" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Settings overrides" }), '{{"interval_seconds": 300}');
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).toHaveBeenCalledWith(
      { repo: "acme/gadgets", body: { logql: '{app="gadgets"}', tracker: "github", tracker_target: {}, settings: { interval_seconds: 300 }, enabled: true } },
      expect.anything(),
    );
    expect(toast.success).toHaveBeenCalledWith("Saved acme/gadgets");
  });

  it("edits a stack with the repository fixed and the linear target fields filled", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    expect(screen.getByRole("dialog", { name: "Edit acme/widgets" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Repository" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Linear team id" })).toHaveValue("team-1");
    expect(screen.getByRole("textbox", { name: "Linear project id" })).toHaveValue("proj-1");
  });

  it("refuses settings that are not an object", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const settings = screen.getByRole("textbox", { name: "Settings overrides" });
    await userEvent.clear(settings);
    await userEvent.type(settings, "[1, 2]");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Settings overrides must be a JSON object, such as {\"interval_seconds\": 600}.");
  });

  it("names the overrides the server will ignore before saving, and after", async () => {
    save.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) => opts.onSuccess({ ...STACK, ignored_settings: ["colour"] }));
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const settings = screen.getByRole("textbox", { name: "Settings overrides" });
    await userEvent.clear(settings);
    await userEvent.type(settings, '{{"colour": 1}');
    expect(screen.getByText("Not a watcher setting, will be dropped: colour")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(toast.info).toHaveBeenCalledWith("Dropped settings the watcher does not know: colour");
  });

  it("disables and enables from the row's menu, and deletes behind a confirm", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Disable" }));
    expect(save).toHaveBeenCalledWith(
      { repo: "acme/widgets", body: { logql: STACK.logql, tracker: STACK.tracker, tracker_target: STACK.tracker_target, settings: STACK.settings, enabled: false } },
      expect.anything(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Stop watching acme/widgets?" });
    expect(dialog).toHaveTextContent(/runs, signatures and cases stay/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("acme/widgets", expect.anything());
  });

  it("offers the writes disabled to an operator session", async () => {
    useAuthStore.setState({ authMode: "session", user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    render(<WatcherStacksTab />);
    expect(screen.getByRole("button", { name: "Add stack" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeDisabled();
  });

  it("shows honest empty and error states", () => {
    mockUseWatcherStacks.mockReturnValue(ready({ stacks: [], overridable_settings: [] }));
    render(<WatcherStacksTab />);
    expect(screen.getByText("No watched stacks")).toBeInTheDocument();
    mockUseWatcherStacks.mockReturnValue({ data: undefined, isPending: false, isError: true, refetch: vi.fn() });
    render(<WatcherStacksTab />);
    expect(screen.getByText("Failed to load watched stacks")).toBeInTheDocument();
  });
});
```

(`{{` in `userEvent.type` is how a literal `{` is typed.) In `WatcherPage.test.tsx`, add `vi.mock("@/hooks/useWatcherStacks", () => ({ useWatcherStacks: () => ({ data: { stacks: [], overridable_settings: [] }, isPending: false, isError: false, refetch: vi.fn() }), useSaveWatcherStack: () => ({ mutate: vi.fn(), isPending: false }), useDeleteWatcherStack: () => ({ mutate: vi.fn(), isPending: false }) }))` and a test:

```tsx
  it("opens the Stacks tab from the URL and keeps the ticks as the default", async () => {
    renderPage();
    expect(screen.getByRole("tab", { name: "Ticks" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("table")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: "Stacks" }));
    expect(screen.getByText("No watched stacks")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/api/__tests__/watcher.test.ts src/components/watcher/__tests__/WatcherStacksTab.test.tsx src/pages/__tests__/WatcherPage.test.tsx`
Expected: the two new files fail to resolve their modules; the Watcher page test fails on the missing tabs.

- [ ] **Step 3: Types, api, hooks and the tab list**

Append to `frontend/src/lib/types/watcher.ts`:

```ts
export type WatcherTracker = "linear" | "github" | "gitlab";

/** One stack embry0 watches (#378): a repository, its Loki selector, where findings are filed, and the settings it overrides. */
export interface WatcherStack {
  repo: string;
  logql: string;
  tracker: WatcherTracker;
  /** linear: `{team_id, project_id}`; github and gitlab file into the stack's repository. */
  tracker_target: Record<string, unknown>;
  /** Overrides only; an absent key inherits the global default. */
  settings: Record<string, unknown>;
  enabled: boolean;
}

export interface WatcherStackList {
  stacks: WatcherStack[];
  overridable_settings: string[];
}

export type WatcherStackWrite = Omit<WatcherStack, "repo">;

/** What PUT answers: the stored stack and the settings keys it dropped. */
export interface WatcherStackSaved extends WatcherStack {
  ignored_settings: string[];
}
```

Append to `frontend/src/api/watcher.ts` (extend its type import):

```ts
export async function fetchWatcherStacks(): Promise<WatcherStackList> {
  const { data } = await api.get<WatcherStackList>("/watcher/stacks");
  return data;
}

function stackPath(repo: string): string {
  const [owner, ...rest] = repo.split("/");
  return `/watcher/stacks/${encodeURIComponent(owner)}/${encodeURIComponent(rest.join("/"))}`;
}

/** Create or replace a watched stack (admin). A refusal (403, 422) is shown in the editor. */
export async function putWatcherStack(repo: string, body: WatcherStackWrite): Promise<WatcherStackSaved> {
  const { data } = await api.put<WatcherStackSaved>(stackPath(repo), body, { suppressValidationToast: true });
  return data;
}

/** Stop watching a stack; its runs, signatures and cases stay. */
export async function deleteWatcherStack(repo: string): Promise<void> {
  await api.delete(stackPath(repo));
}
```

`frontend/src/hooks/useWatcherStacks.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteWatcherStack, fetchWatcherStacks, putWatcherStack } from "@/api/watcher";
import type { WatcherStackWrite } from "@/lib/types";

export function useWatcherStacks() {
  return useQuery({ queryKey: ["watcher-stacks"], queryFn: fetchWatcherStacks });
}

export function useSaveWatcherStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ repo, body }: { repo: string; body: WatcherStackWrite }) => putWatcherStack(repo, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watcher-stacks"] }),
  });
}

export function useDeleteWatcherStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo: string) => deleteWatcherStack(repo),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watcher-stacks"] }),
  });
}
```

`frontend/src/lib/watcherTabs.ts`:

```ts
/** The Watcher page's tabs; the open one rides in the URL (`/watcher?tab=stacks`). */
export const WATCHER_TABS = [
  { value: "ticks", label: "Ticks" },
  { value: "stacks", label: "Stacks" },
] as const;

export type WatcherTab = (typeof WATCHER_TABS)[number];

export function watcherTab(value: string | null): WatcherTab {
  return WATCHER_TABS.find((tab) => tab.value === value) ?? WATCHER_TABS[0];
}
```

- [ ] **Step 4: The editor and the tab**

`frontend/src/components/watcher/StackEditorDialog.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { useSaveWatcherStack } from "@/hooks/useWatcherStacks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import type { WatcherStack, WatcherTracker } from "@/lib/types";

const TRACKERS: { value: WatcherTracker; label: string }[] = [
  { value: "linear", label: "Linear" },
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
];

const SETTINGS_SHAPE = 'Settings overrides must be a JSON object, such as {"interval_seconds": 600}.';

/** The overrides as typed: an object, or the reason it is not one. */
function parseSettings(text: string): { settings: Record<string, unknown> } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === "") return { settings: {} };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return { error: SETTINGS_SHAPE };
    return { settings: parsed as Record<string, unknown> };
  } catch {
    return { error: SETTINGS_SHAPE };
  }
}

function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Admin scope required.";
  if (response?.status === 422) return formatErrorDetail(response.data?.detail) ?? "The stack was not saved.";
  return null;
}

interface Props {
  /** The stack to edit; absent, the editor adds one. */
  stack?: WatcherStack;
  overridable: string[];
  onClose: () => void;
}

/**
 * Add or edit a watched stack (spec 3.7, "Watcher stacks"): the repository,
 * its Loki selector, the tracker findings are filed in (Linear needs a team
 * and a project), the settings it overrides as a JSON object, and whether it
 * is enabled. Keys the watcher does not know are named before the save and
 * again from the server's answer, which drops them.
 */
export function StackEditorDialog({ stack, overridable, onClose }: Props) {
  const [repo, setRepo] = useState(stack?.repo ?? "");
  const [logql, setLogql] = useState(stack?.logql ?? "");
  const [tracker, setTracker] = useState<WatcherTracker>(stack?.tracker ?? "linear");
  const [teamId, setTeamId] = useState(String(stack?.tracker_target.team_id ?? ""));
  const [projectId, setProjectId] = useState(String(stack?.tracker_target.project_id ?? ""));
  const [settingsText, setSettingsText] = useState(
    stack && Object.keys(stack.settings).length > 0 ? JSON.stringify(stack.settings, null, 2) : "",
  );
  const [enabled, setEnabled] = useState(stack?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const save = useSaveWatcherStack();

  const parsed = parseSettings(settingsText);
  const unknown = "settings" in parsed ? Object.keys(parsed.settings).filter((k) => !overridable.includes(k)) : [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const name = repo.trim().toLowerCase();
    if (!/^[^/\s]+\/[^/\s]+$/.test(name)) {
      setError("Name the repository as owner/name.");
      return;
    }
    if (!logql.trim()) {
      setError("Give the stack a LogQL selector.");
      return;
    }
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setError(null);
    const tracker_target = tracker === "linear" ? { team_id: teamId.trim(), project_id: projectId.trim() } : {};
    save.mutate(
      { repo: name, body: { logql: logql.trim(), tracker, tracker_target, settings: parsed.settings, enabled } },
      {
        onSuccess: (saved) => {
          toast.success(`Saved ${saved.repo}`);
          if (saved.ignored_settings.length > 0) toast.info(`Dropped settings the watcher does not know: ${saved.ignored_settings.join(", ")}`);
          onClose();
        },
        onError: (err) => {
          const shown = refusal(err);
          if (shown) setError(shown);
          else toastApiError(err, "The stack was not saved");
        },
      },
    );
  };

  return (
    <Dialog onClose={() => !save.isPending && onClose()} title={stack ? `Edit ${stack.repo}` : "Add stack"} dismissOnBackdrop={false} className="max-w-xl">
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="space-y-1">
          <Label htmlFor="stack-repo">Repository</Label>
          <Input id="stack-repo" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="owner/name" disabled={!!stack || save.isPending} data-autofocus={!stack ? true : undefined} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="stack-logql">LogQL selector</Label>
          <Textarea id="stack-logql" value={logql} onChange={(e) => setLogql(e.target.value)} rows={2} className="font-mono text-xs" disabled={save.isPending} data-autofocus={stack ? true : undefined} />
          <p className="text-xs text-fg-muted">What the watcher reads from Loki for this stack, such as a container or app label.</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="stack-tracker">Tracker</Label>
            <Select id="stack-tracker" value={tracker} onChange={(e) => setTracker(e.target.value as WatcherTracker)} disabled={save.isPending}>
              {TRACKERS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
          </div>
          {tracker === "linear" && (
            <>
              <div className="space-y-1">
                <Label htmlFor="stack-team">Linear team id</Label>
                <Input id="stack-team" value={teamId} onChange={(e) => setTeamId(e.target.value)} disabled={save.isPending} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="stack-project">Linear project id</Label>
                <Input id="stack-project" value={projectId} onChange={(e) => setProjectId(e.target.value)} disabled={save.isPending} />
              </div>
            </>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="stack-settings">Settings overrides</Label>
          <Textarea id="stack-settings" value={settingsText} onChange={(e) => setSettingsText(e.target.value)} rows={4} className="font-mono text-xs" placeholder='{"interval_seconds": 600}' disabled={save.isPending} />
          <p className="text-xs text-fg-muted">A JSON object. An absent key inherits the global default. Known keys: {overridable.join(", ") || "none"}.</p>
          {unknown.length > 0 && <p className="text-xs text-warning">Not a watcher setting, will be dropped: {unknown.join(", ")}</p>}
        </div>
        <div className="flex items-center gap-3">
          <Switch id="stack-enabled" checked={enabled} onCheckedChange={setEnabled} disabled={save.isPending} label="Enabled" />
          <span className="text-sm text-fg-secondary" aria-hidden="true">Enabled</span>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
```

If `text-warning` is not a token in `index.css`, use `text-fg-secondary` for the dropped-keys line.

`frontend/src/components/watcher/WatcherStacksTab.tsx`:

```tsx
import { useState } from "react";
import { RadioTower } from "lucide-react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { useDeleteWatcherStack, useSaveWatcherStack, useWatcherStacks } from "@/hooks/useWatcherStacks";
import { AdminButton } from "@/components/auth/AdminButton";
import { StackEditorDialog } from "@/components/watcher/StackEditorDialog";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Menu } from "@/components/ui/Menu";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { providerLabel } from "@/lib/status";
import { useIsAdmin } from "@/stores/authStore";
import type { WatcherStack, WatcherStackWrite } from "@/lib/types";

type Open = { kind: "add" } | { kind: "edit"; stack: WatcherStack } | { kind: "delete"; stack: WatcherStack } | null;

/** The PUT body of a stored stack: everything but its name. */
function stackBody(stack: WatcherStack, enabled: boolean): WatcherStackWrite {
  return { logql: stack.logql, tracker: stack.tracker, tracker_target: stack.tracker_target, settings: stack.settings, enabled };
}

/**
 * The stacks embry0 watches (spec 3.7): readable by every tier, like the
 * route; adding, editing, enabling, disabling and deleting are an admin's.
 * Deleting stops the watching and keeps the record of what it found.
 */
export function WatcherStacksTab() {
  const [open, setOpen] = useState<Open>(null);
  const isAdmin = useIsAdmin();
  const list = useWatcherStacks();
  const save = useSaveWatcherStack();
  const del = useDeleteWatcherStack();

  const toggle = (stack: WatcherStack) =>
    save.mutate(
      { repo: stack.repo, body: stackBody(stack, !stack.enabled) },
      {
        onSuccess: () => toast.success(`${stack.enabled ? "Disabled" : "Enabled"} ${stack.repo}`),
        onError: (e) => toastApiError(e, `Could not ${stack.enabled ? "disable" : "enable"} ${stack.repo}`),
      },
    );

  const remove = (stack: WatcherStack) =>
    del.mutate(stack.repo, {
      onSuccess: () => {
        toast.success(`Stopped watching ${stack.repo}`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, `Could not stop watching ${stack.repo}`);
      },
    });

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load watched stacks" onRetry={() => void list.refetch()} />;
  } else if (list.isPending || !list.data) {
    body = <TableSkeleton />;
  } else if (list.data.stacks.length === 0) {
    body = (
      <EmptyState icon={RadioTower} title="No watched stacks" description="Add a stack to point the watcher at a deployment's logs. Nothing is analysed until a stack is enabled." />
    );
  } else {
    body = (
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col" className="hidden sm:table-cell">LogQL</th>
              <th scope="col">Tracker</th>
              <th scope="col">State</th>
              <th scope="col" className="hidden sm:table-cell">Settings</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.stacks.map((stack) => {
              const overrides = Object.keys(stack.settings).length;
              return (
                <tr key={stack.repo} data-testid="watcher-stack-row">
                  <td className="whitespace-nowrap font-mono text-xs">{stack.repo}</td>
                  <td className="hidden w-full max-w-0 truncate font-mono text-xs text-fg-muted sm:table-cell" title={stack.logql}>{stack.logql}</td>
                  <td className="whitespace-nowrap text-fg-secondary">{providerLabel(stack.tracker)}</td>
                  <td className="whitespace-nowrap">
                    <Badge tone={stack.enabled ? "success" : "neutral"}>{stack.enabled ? "Enabled" : "Disabled"}</Badge>
                  </td>
                  <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">
                    {overrides === 0 ? "defaults" : `${overrides} ${overrides === 1 ? "override" : "overrides"}`}
                  </td>
                  <td className="w-px">
                    <Menu
                      label={`Actions for ${stack.repo}`}
                      items={[
                        { label: "Edit", onSelect: () => setOpen({ kind: "edit", stack }), disabled: !isAdmin },
                        { label: stack.enabled ? "Disable" : "Enable", onSelect: () => toggle(stack), disabled: !isAdmin || save.isPending },
                        { label: "Delete", onSelect: () => setOpen({ kind: "delete", stack }), destructive: true, disabled: !isAdmin },
                      ]}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          What the watcher reads and where it files what it finds. Adding a stack points embry0&apos;s log analysis, and the spend behind it, at another deployment.
        </p>
        <AdminButton type="button" variant="outline" size="sm" onClick={() => setOpen({ kind: "add" })}>
          Add stack
        </AdminButton>
      </div>
      {body}
      {(open?.kind === "add" || open?.kind === "edit") && (
        <StackEditorDialog
          stack={open.kind === "edit" ? open.stack : undefined}
          overridable={list.data?.overridable_settings ?? []}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "delete" && (
        <ConfirmDialog
          title={`Stop watching ${open.stack.repo}?`}
          confirmLabel="Delete"
          destructive
          pending={del.isPending}
          onConfirm={() => remove(open.stack)}
          onClose={() => setOpen(null)}
        >
          The watcher stops reading this stack. Its runs, signatures and cases stay: they are the record of what it found.
        </ConfirmDialog>
      )}
    </div>
  );
}
```

`providerLabel` (`@/lib/status`) spells `linear`, `github` and `gitlab` as their makers do; if it does not know `linear`, add `linear: "Linear"` to `PROVIDER_LABELS` in that file (it is a label table, not a surface of this plan).

`frontend/src/pages/WatcherPage.tsx`: import `useSearchParams`, the `Tabs` family, `WATCHER_TABS`, `watcherTab` and `WatcherStacksTab`. The page keeps its `h1` row (title and Run tick) and its intro paragraph; under them:

```tsx
  // The open tab lives in the URL so a stack can be linked to.
  const [params, setParams] = useSearchParams();
  const tab = watcherTab(params.get("tab")).value;
  const openTab = (value: string) => setParams(value === WATCHER_TABS[0].value ? {} : { tab: value }, { replace: true });
```

```tsx
      <Tabs defaultValue={WATCHER_TABS[0].value} value={tab} onValueChange={openTab}>
        <TabsList>
          {WATCHER_TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="ticks">{/* the existing runs block: PageError / TableSkeleton / EmptyState / table */}</TabsContent>
        <TabsContent value="stacks">
          <WatcherStacksTab />
        </TabsContent>
      </Tabs>
```

Keep the Run-tick button on the title row (it belongs to the ticks, and the existing tests find it there).

- [ ] **Step 5: Run the tests to verify they pass, then the gate**

Run the Step 2 command: every file passes. Then the frontend gate; read the summary lines.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types/watcher.ts frontend/src/api/watcher.ts frontend/src/api/__tests__/watcher.test.ts frontend/src/hooks/useWatcherStacks.ts frontend/src/lib/watcherTabs.ts frontend/src/components/watcher frontend/src/pages/WatcherPage.tsx frontend/src/pages/__tests__/WatcherPage.test.tsx frontend/src/lib/status.ts
git commit -m "feat(dashboard): the Stacks tab on the Watcher page

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

### Task 9: Organization actions and the Settings tab (issue Q)

**Files:**
- Modify: `frontend/src/api/orgs.ts` (append five calls and their types; `OrgSettingsPatch` gains `auto_register`), `frontend/src/api/__tests__/orgs.test.ts` (append)
- Modify: `frontend/src/hooks/useOrgs.ts` (append five hooks)
- Create: `frontend/src/lib/orgTabs.ts`
- Create: `frontend/src/components/orgs/OrgSettingsTab.tsx`, `frontend/src/components/orgs/__tests__/OrgSettingsTab.test.tsx`
- Modify: `frontend/src/pages/OrganizationPage.tsx` (`useIsAdmin`, the Probe-again button, the tabs), `frontend/src/pages/__tests__/OrganizationPage.test.tsx` (two tests), `frontend/src/pages/__tests__/OrganizationPage.goldenTouch.test.tsx` (nothing to add; it must stay green)

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 1), `Switch`, `Tabs` family, `AdminButton`, `useIsAdmin`, `Input`, `Label`, `Badge`, `Button`, `Skeleton`, `useSearchParams`, `formatErrorDetail`, `toastApiError`, `toast`, the `org()` fixture (`@/components/orgs/__tests__/fixtures`).
- Produces: `useOrganizationWebhook(owner, enabled)`, `useInstallOrganizationWebhook(owner)`, `useReprobeOrganization(owner)`, `useSetOrganizationStatus(owner)`, `useUpdateOrganization(owner)`; `ORG_TABS`, `orgTab(value)`.

The routes (`embry0/api/v1/organizations.py`, `docs/api.md:381-407`): `POST /orgs/{owner}/probe` (admin, no body) re-reads what the credential can do and answers the organization; `PATCH /orgs/{owner}` (admin) takes `{display_name?, settings?}` with `extra="forbid"`, settings limited to `auto_register: bool` and `rediscover_hours: int | null`, and answers the organization; `PUT /orgs/{owner}/status` (admin) takes `{status: active|paused|removed}`; `GET /orgs/{owner}/webhook` (any tier) answers `{owner, installed, hook, target_url, hooks_visible, hooks: [{id, url, events, active}]}`; `POST /orgs/{owner}/webhook` (admin, GitHub only) answers `{owner, target_url, outcome: done|already|failed, hook, detail}`, 409 when the organization is not active, 422 for a user account, a credential without hook administration, no public webhook origin or an empty webhook secret, 502 when GitHub refused. The page keeps `RediscoverSetting` in its header (`OrganizationPage.test.tsx`, "shows the rediscovery schedule in the header") and its one gold on Apply (`goldenTouch`).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/__tests__/orgs.test.ts` (following its `api` mock; add `put` and `patch` to the mock if missing):

```ts
  it("probes again, patches the profile, sets the status and reads and installs the webhook at the contract's paths", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { owner: "acme" } });
    (api.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { owner: "acme" } });
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { owner: "acme" } });
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { owner: "acme", installed: false, hook: null, target_url: null, hooks_visible: true, hooks: [] } });
    await reprobeOrganization("acme");
    expect(api.post).toHaveBeenLastCalledWith("/orgs/acme/probe", undefined, { suppressValidationToast: true });
    await updateOrganization("acme", { display_name: "Acme", settings: { auto_register: true } });
    expect(api.patch).toHaveBeenLastCalledWith("/orgs/acme", { display_name: "Acme", settings: { auto_register: true } }, { suppressValidationToast: true });
    await setOrganizationStatus("acme", "paused");
    expect(api.put).toHaveBeenLastCalledWith("/orgs/acme/status", { status: "paused" }, { suppressValidationToast: true });
    await fetchOrganizationWebhook("acme");
    expect(api.get).toHaveBeenLastCalledWith("/orgs/acme/webhook");
    await installOrganizationWebhook("acme");
    expect(api.post).toHaveBeenLastCalledWith("/orgs/acme/webhook", undefined, { suppressValidationToast: true });
  });
```

`frontend/src/components/orgs/__tests__/OrgSettingsTab.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockUseWebhook = vi.fn();
const update = vi.fn();
const setStatus = vi.fn();
const install = vi.fn();
vi.mock("@/hooks/useOrgs", () => ({
  useOrganizationWebhook: (owner: string, enabled: boolean) => mockUseWebhook(owner, enabled),
  useUpdateOrganization: () => ({ mutate: update, isPending: false }),
  useSetOrganizationStatus: () => ({ mutate: setStatus, isPending: false }),
  useInstallOrganizationWebhook: () => ({ mutate: install, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { OrgSettingsTab } from "../OrgSettingsTab";
import { org } from "./fixtures";

const WEBHOOK = {
  owner: "acme",
  installed: true,
  hook: { id: 7, url: "https://hooks.example.test/webhooks/github", events: ["issues", "pull_request"], active: true },
  target_url: "https://hooks.example.test/webhooks/github",
  hooks_visible: true,
  hooks: [
    { id: 7, url: "https://hooks.example.test/webhooks/github", events: ["issues", "pull_request"], active: true },
    { id: 9, url: "https://other.example.test", events: ["push"], active: false },
  ],
};

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

beforeEach(() => {
  [update, setStatus, install].forEach((m) => m.mockReset());
  mockUseWebhook.mockReset().mockReturnValue(ready(WEBHOOK));
});

describe("OrgSettingsTab", () => {
  it("saves a display name, and sends null to clear it", async () => {
    render(<OrgSettingsTab org={org({ display_name: "Acme Inc" })} isAdmin />);
    const name = screen.getByRole("textbox", { name: "Display name" });
    expect(name).toHaveValue("Acme Inc");
    await userEvent.clear(name);
    await userEvent.type(name, "Acme");
    await userEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(update).toHaveBeenCalledWith({ display_name: "Acme" }, expect.anything());
    await userEvent.clear(name);
    await userEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(update).toHaveBeenLastCalledWith({ display_name: null }, expect.anything());
  });

  it("switches automatic registration on through the settings patch", async () => {
    render(<OrgSettingsTab org={org()} isAdmin />);
    const auto = screen.getByRole("switch", { name: "Register repositories automatically" });
    expect(auto).toHaveAttribute("aria-checked", "false");
    await userEvent.click(auto);
    expect(update).toHaveBeenCalledWith({ settings: { auto_register: true } }, expect.anything());
  });

  it("shows whether the webhook points here, lists the hooks the credential sees, and installs one", async () => {
    install.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ owner: "acme", target_url: WEBHOOK.target_url, outcome: "already", hook: WEBHOOK.hook, detail: "" }),
    );
    render(<OrgSettingsTab org={org()} isAdmin />);
    const section = screen.getByTestId("org-webhook");
    expect(within(section).getByText("Installed")).toBeInTheDocument();
    expect(within(section).getAllByRole("listitem")).toHaveLength(2);
    expect(within(section).getByText("https://other.example.test")).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Install webhook" }));
    expect(install).toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalledWith("A webhook already points at this deployment.");
  });

  it("does not read or offer the webhook for a GitLab group", () => {
    render(<OrgSettingsTab org={org({ forge: "gitlab" })} isAdmin />);
    expect(mockUseWebhook).toHaveBeenLastCalledWith("acme", false);
    expect(screen.getByTestId("org-webhook")).toHaveTextContent("Organization webhooks are a GitHub feature.");
  });

  it("pauses and resumes without a confirm, and removes behind one", async () => {
    render(<OrgSettingsTab org={org()} isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(setStatus).toHaveBeenCalledWith("paused", expect.anything());
    await userEvent.click(screen.getByRole("button", { name: "Remove organization" }));
    const dialog = screen.getByRole("dialog", { name: "Remove acme?" });
    expect(dialog).toHaveTextContent(/repositories stay registered/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(setStatus).toHaveBeenLastCalledWith("removed", expect.anything());
  });

  it("offers Resume to a paused owner and Restore to a removed one", async () => {
    render(<OrgSettingsTab org={org({ status: "paused" })} isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(setStatus).toHaveBeenCalledWith("active", expect.anything());
    render(<OrgSettingsTab org={org({ status: "removed" })} isAdmin />);
    expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove organization" })).toBeNull();
  });

  it("offers every write disabled below admin and spends no gold", () => {
    render(<OrgSettingsTab org={org()} isAdmin={false} />);
    expect(screen.getByRole("button", { name: "Save name" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Register repositories automatically" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Install webhook" })).toBeDisabled();
    const gold = [...document.querySelectorAll("*")].filter((el) => /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/.test(el.getAttribute("class") ?? ""));
    expect(gold).toHaveLength(0);
  });
});
```

Append to `frontend/src/pages/__tests__/OrganizationPage.test.tsx` (its `@/api/orgs` mock is `importActual` plus overrides, so the new api functions resolve; add `reprobe: vi.fn()` to its `api` object and `reprobeOrganization: (...a: unknown[]) => api.reprobe(...a)` to the mock; add `fetchOrganizationWebhook: () => Promise.resolve({ owner: "acme", installed: false, hook: null, target_url: null, hooks_visible: false, hooks: [] })`):

```tsx
  it("probes again from the header", async () => {
    api.reprobe.mockResolvedValue(org({ discovered_at: "2026-01-01T00:00:00Z" }));
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "acme" });
    fireEvent.click(screen.getByRole("button", { name: "Probe again" }));
    await waitFor(() => expect(api.reprobe).toHaveBeenCalledWith("acme"));
  });

  it("opens the Settings tab from the URL and keeps the overview as the default", async () => {
    renderPage("/orgs/acme?tab=settings");
    await screen.findByRole("heading", { level: 1, name: "acme" });
    expect(screen.getByRole("tab", { name: "Settings" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("textbox", { name: "Display name" })).toBeInTheDocument();
    expect(screen.queryByTestId("org-act-panel")).toBeNull();
  });
```

(the `renderPage` helper takes a path already; the route stays `/orgs/:owner`).

- [ ] **Step 2: Run the tests to verify they fail**

Run, from `frontend/`: `npx vitest run src/api/__tests__/orgs.test.ts src/components/orgs/__tests__/OrgSettingsTab.test.tsx src/pages/__tests__/OrganizationPage.test.tsx src/pages/__tests__/OrganizationPage.goldenTouch.test.tsx`
Expected: the api test fails on missing exports; the settings-tab file fails to resolve; the two page tests fail on the missing button and tab; the golden-touch tests pass as before.

- [ ] **Step 3: Api, hooks and the tab list**

Append to `frontend/src/api/orgs.ts` (and give `OrgSettingsPatch` a field `/** Register a repository under this owner when a webhook delivery names one nobody registered. */ auto_register?: boolean;`):

```ts
/** One webhook on the organization, as GitHub reports it; a hook pointing elsewhere is reduced to its origin. */
export interface OrgHook {
  id: number | null;
  url: string;
  events: string[];
  active: boolean;
}

export interface OrgWebhookStatus {
  owner: string;
  installed: boolean;
  hook: OrgHook | null;
  /** Where this deployment's hook points, or null without a public webhook origin. */
  target_url: string | null;
  /** False when the credential cannot list hooks; `hooks` is then empty and says nothing. */
  hooks_visible: boolean;
  hooks: OrgHook[];
}

export interface OrgWebhookInstall {
  owner: string;
  target_url: string;
  outcome: "done" | "already" | "failed";
  hook: OrgHook | null;
  detail: string;
}

/** What PATCH /orgs/{owner} takes: a display name (null clears), settings (merged), or both. */
export interface OrgPatch {
  display_name?: string | null;
  settings?: OrgSettingsPatch;
}

/** Ask the forge again what the stored credential can do at this owner (admin). */
export async function reprobeOrganization(owner: string): Promise<Organization> {
  const { data } = await api.post<Organization>(`/orgs/${encodeURIComponent(owner)}/probe`, undefined, { suppressValidationToast: true });
  return data;
}

/** Change the display name or the settings (admin); a refusal is the caller's to show. */
export async function updateOrganization(owner: string, body: OrgPatch): Promise<Organization> {
  const { data } = await api.patch<Organization>(`/orgs/${encodeURIComponent(owner)}`, body, { suppressValidationToast: true });
  return data;
}

export async function setOrganizationStatus(owner: string, status: OrgStatus): Promise<Organization> {
  const { data } = await api.put<Organization>(`/orgs/${encodeURIComponent(owner)}/status`, { status }, { suppressValidationToast: true });
  return data;
}

export async function fetchOrganizationWebhook(owner: string): Promise<OrgWebhookStatus> {
  const { data } = await api.get<OrgWebhookStatus>(`/orgs/${encodeURIComponent(owner)}/webhook`);
  return data;
}

/** Install the organization's one webhook, pointing at this deployment (admin, GitHub only). */
export async function installOrganizationWebhook(owner: string): Promise<OrgWebhookInstall> {
  const { data } = await api.post<OrgWebhookInstall>(`/orgs/${encodeURIComponent(owner)}/webhook`, undefined, { suppressValidationToast: true });
  return data;
}
```

Append to `frontend/src/hooks/useOrgs.ts` (extend its import):

```ts
/** The organization's webhook as the forge reports it; read only for a GitHub owner on a tab that shows it. */
export function useOrganizationWebhook(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["orgs", owner, "webhook"],
    queryFn: () => fetchOrganizationWebhook(owner),
    enabled: !!owner && enabled,
    retry: false,
  });
}

function replaceOrganization(qc: ReturnType<typeof useQueryClient>, owner: string) {
  return {
    onSuccess: (org: Organization) => qc.setQueryData(["orgs", owner], org),
    onSettled: () => qc.invalidateQueries({ queryKey: ["orgs"] }),
  };
}

export function useReprobeOrganization(owner: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => reprobeOrganization(owner), ...replaceOrganization(qc, owner) });
}

export function useUpdateOrganization(owner: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: OrgPatch) => updateOrganization(owner, body), ...replaceOrganization(qc, owner) });
}

export function useSetOrganizationStatus(owner: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (status: OrgStatus) => setOrganizationStatus(owner, status), ...replaceOrganization(qc, owner) });
}

export function useInstallOrganizationWebhook(owner: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => installOrganizationWebhook(owner),
    onSettled: () => qc.invalidateQueries({ queryKey: ["orgs", owner, "webhook"] }),
  });
}
```

`frontend/src/lib/orgTabs.ts`:

```ts
/** An organization page's tabs; the open one rides in the URL (`/orgs/acme?tab=settings`). */
export const ORG_TABS = [
  { value: "overview", label: "Overview" },
  { value: "settings", label: "Settings" },
] as const;

export type OrgTab = (typeof ORG_TABS)[number];

export function orgTab(value: string | null): OrgTab {
  return ORG_TABS.find((tab) => tab.value === value) ?? ORG_TABS[0];
}
```

- [ ] **Step 4: The Settings tab**

`frontend/src/components/orgs/OrgSettingsTab.tsx`:

```tsx
import { useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import type { Organization, OrgStatus } from "@/api/orgs";
import { useInstallOrganizationWebhook, useOrganizationWebhook, useSetOrganizationStatus, useUpdateOrganization } from "@/hooks/useOrgs";
import { Badge } from "@/components/ui/Badge";
import { Button, type ButtonProps } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Switch } from "@/components/ui/Switch";
import { Tooltip } from "@/components/ui/Tooltip";
import { statusLabel } from "@/lib/status";

function refusal(err: unknown, fallback: string): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Admin scope required.";
  return formatErrorDetail(response?.data?.detail) ?? fallback;
}

function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="space-y-2 rounded-lg border border-border p-4" data-testid={testId}>
      <h2 className="text-section font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** An admin's button, disabled with the reason on hover for everyone else, in the page's non-gold variants. */
function Gated({ isAdmin, children, ...props }: { isAdmin: boolean; children: ReactNode } & ButtonProps) {
  const button = (
    <Button type="button" size="sm" {...props} variant={props.variant ?? "outline"} disabled={props.disabled || !isAdmin}>
      {children}
    </Button>
  );
  return isAdmin ? button : <Tooltip content="Admin scope required">{button}</Tooltip>;
}

/**
 * An organization's settings (spec 3.7, issue Q): its display name, whether a
 * webhook delivery registers a repository nobody registered, the webhook on
 * the organization itself, and its status. Every write is an admin's; below
 * that the controls are shown disabled. Nothing here is gold: the page's one
 * gold is Apply in the act panel.
 */
export function OrgSettingsTab({ org, isAdmin }: { org: Organization; isAdmin: boolean }) {
  const [name, setName] = useState(org.display_name ?? "");
  const [nameError, setNameError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const update = useUpdateOrganization(org.owner);
  const setStatus = useSetOrganizationStatus(org.owner);
  const install = useInstallOrganizationWebhook(org.owner);
  const github = org.forge === "github";
  const webhook = useOrganizationWebhook(org.owner, github);
  const autoRegister = org.settings.auto_register === true;

  const saveName = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    update.mutate(
      { display_name: trimmed === "" ? null : trimmed },
      { onSuccess: () => setNameError(null), onError: (err) => setNameError(refusal(err, "The name was not saved.")) },
    );
  };

  const toggleAutoRegister = (next: boolean) =>
    update.mutate({ settings: { auto_register: next } }, { onError: (err) => toastApiError(err, "The setting was not saved") });

  const changeStatus = (status: OrgStatus) =>
    setStatus.mutate(status, {
      onSuccess: (saved) => {
        setRemoving(false);
        toast.success(`${saved.display_name || saved.owner} is now ${statusLabel(saved.status).toLowerCase()}`);
      },
      onError: (err) => {
        setRemoving(false);
        toastApiError(err, "The status was not changed");
      },
    });

  const installHook = () =>
    install.mutate(undefined, {
      onSuccess: (result) => {
        if (result.outcome === "done") toast.success("Webhook installed on the organization.");
        else toast.info("A webhook already points at this deployment.");
      },
      onError: (err) => toastApiError(err, "The webhook was not installed"),
    });

  return (
    <div className="space-y-4" data-testid="org-settings">
      <Section title="Name">
        <form onSubmit={saveName} className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="org-display-name">Display name</Label>
            <Input id="org-display-name" value={name} onChange={(e) => setName(e.target.value)} disabled={!isAdmin || update.isPending} placeholder={org.owner} className="w-64" />
          </div>
          <Gated isAdmin={isAdmin} type="submit" disabled={update.isPending}>
            Save name
          </Gated>
          <span className="text-xs text-fg-muted">Empty shows the owner's own name, {org.owner}.</span>
        </form>
        {nameError && (
          <p className="text-sm text-destructive" role="alert">
            {nameError}
          </p>
        )}
      </Section>

      <Section title="Automatic registration">
        <div className="flex items-center gap-3">
          <Switch
            id="org-auto-register"
            checked={autoRegister}
            onCheckedChange={toggleAutoRegister}
            disabled={!isAdmin || update.isPending}
            label="Register repositories automatically"
          />
          <span className="text-sm text-fg-secondary" aria-hidden="true">Register repositories automatically</span>
        </div>
        <p className="text-xs text-fg-muted">
          A webhook delivery for a repository under {org.owner} that nobody registered registers it, bound to this organization's credential, before the delivery is handled. Off, the need is recorded instead.
        </p>
      </Section>

      <Section title="Webhook" testId="org-webhook">
        {!github ? (
          <p className="text-sm text-fg-muted">Organization webhooks are a GitHub feature. A GitLab group's repositories each carry their own.</p>
        ) : webhook.isError ? (
          <p className="text-sm text-destructive">The webhook could not be read from GitHub.</p>
        ) : !webhook.data ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={webhook.data.installed ? "success" : "neutral"}>{webhook.data.installed ? "Installed" : "Not installed"}</Badge>
              {webhook.data.target_url ? (
                <span className="font-mono text-xs text-fg-secondary">{webhook.data.target_url}</span>
              ) : (
                <span className="text-fg-muted">No public webhook origin is configured.</span>
              )}
              <Gated isAdmin={isAdmin} onClick={installHook} disabled={install.isPending || webhook.data.installed || org.status !== "active"}>
                {install.isPending ? "Installing…" : "Install webhook"}
              </Gated>
            </div>
            {!webhook.data.hooks_visible ? (
              <p className="text-xs text-fg-muted">The credential cannot list the organization's hooks.</p>
            ) : webhook.data.hooks.length === 0 ? (
              <p className="text-xs text-fg-muted">No webhooks on the organization.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {webhook.data.hooks.map((hook) => (
                  <li key={hook.id ?? hook.url} className="flex flex-wrap items-center gap-2 font-mono text-fg-secondary">
                    <span>{hook.url}</span>
                    <span className="text-fg-muted">{`${hook.events.length} ${hook.events.length === 1 ? "event" : "events"}${hook.active ? "" : ", inactive"}`}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      <Section title="Status">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={org.status === "active" ? "success" : org.status === "paused" ? "warning" : "neutral"}>{statusLabel(org.status)}</Badge>
          {org.status === "active" && (
            <Gated isAdmin={isAdmin} onClick={() => changeStatus("paused")} disabled={setStatus.isPending}>
              Pause
            </Gated>
          )}
          {org.status === "paused" && (
            <Gated isAdmin={isAdmin} onClick={() => changeStatus("active")} disabled={setStatus.isPending}>
              Resume
            </Gated>
          )}
          {org.status === "removed" ? (
            <Gated isAdmin={isAdmin} onClick={() => changeStatus("active")} disabled={setStatus.isPending}>
              Restore
            </Gated>
          ) : (
            <Gated isAdmin={isAdmin} variant="destructive" onClick={() => setRemoving(true)} disabled={setStatus.isPending}>
              Remove organization
            </Gated>
          )}
        </div>
        <p className="text-xs text-fg-muted">
          Paused, discovery and the schedule stop and the webhook gate records needs without acting. Removed, the owner leaves the lists; Restore brings it back.
        </p>
      </Section>

      {removing && (
        <ConfirmDialog
          title={`Remove ${org.owner}?`}
          confirmLabel="Remove"
          destructive
          pending={setStatus.isPending}
          onConfirm={() => changeStatus("removed")}
          onClose={() => setRemoving(false)}
        >
          The owner leaves the organization lists and discovery stops. Its repositories stay registered and keep working; Restore on this tab brings the owner back.
        </ConfirmDialog>
      )}
    </div>
  );
}
```

If the `destructive` button variant contains a gold class in `Button.tsx` (it does not today: `bg-destructive`), keep it; the golden-touch test scans `org-page` for `bg-primary`, `bg-primary-dim` and `text-primary` only.

- [ ] **Step 5: The page**

In `frontend/src/pages/OrganizationPage.tsx`:

- Replace the three auth lines (`authMode`, `userTier`, `isAdmin`) with `const isAdmin = useIsAdmin();` and import `useIsAdmin` from `@/stores/authStore` (drop the `useAuthStore` import if unused).
- Import `useSearchParams`, the `Tabs` family, `ORG_TABS`, `orgTab`, `OrgSettingsTab`, `useReprobeOrganization`, `toastApiError` and `ShieldCheck` from lucide.
- Beside `const discover = useDiscoverOrganization(owner);` add `const reprobe = useReprobeOrganization(owner);` and the tab state:

```tsx
  // The open tab lives in the URL so a setting can be linked to.
  const [params, setParams] = useSearchParams();
  const tab = orgTab(params.get("tab")).value;
  const openTab = (value: string) => setParams(value === ORG_TABS[0].value ? {} : { tab: value }, { replace: true });
```

- In the header's button row, before the Rediscover button:

```tsx
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() => reprobe.mutate(undefined, { onError: (e) => toastApiError(e, "The probe did not run") })}
            disabled={!isAdmin || reprobe.isPending}
            data-testid="org-reprobe"
          >
            <ShieldCheck className="h-4 w-4" /> {reprobe.isPending ? "Probing…" : "Probe again"}
          </Button>
```

and drop `className="ml-auto"` from the Rediscover button (the two sit together at the right).

- Wrap the five `Section`s in tabs, after the header `div`:

```tsx
      <Tabs defaultValue={ORG_TABS[0].value} value={tab} onValueChange={openTab}>
        <TabsList>
          {ORG_TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview" className="mt-6 space-y-6">
          {/* the five existing Sections, unchanged */}
        </TabsContent>
        <TabsContent value="settings" className="mt-6">
          <OrgSettingsTab org={o} isAdmin={isAdmin} />
        </TabsContent>
      </Tabs>
```

`RediscoverSetting`, the access chips, the credential line and the discovery error stay in the header.

- [ ] **Step 6: Run the tests to verify they pass, then the gate**

Run the Step 2 command: every file passes, the golden-touch tests included. Then the frontend gate; read the summary lines.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/orgs.ts frontend/src/api/__tests__/orgs.test.ts frontend/src/hooks/useOrgs.ts frontend/src/lib/orgTabs.ts frontend/src/components/orgs/OrgSettingsTab.tsx frontend/src/components/orgs/__tests__/OrgSettingsTab.test.tsx frontend/src/pages/OrganizationPage.tsx frontend/src/pages/__tests__/OrganizationPage.test.tsx
git commit -m "feat(dashboard): organization actions and a Settings tab on the organization page

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ"
```

---

## Deploy notes

Frontend image for everything (rebuilt by `infra/deploy.sh`); orchestrator image for Task 2's route change. No migration, no sandbox image rebuild, no compose change, no new API route, so "What each ingress reaches" in `docs/ops/deploy.md` is unchanged: the dashboard tunnel already publishes the SPA and `/api/v1/*`.

## Live check after integration

On a deployment through the SSH tunnel recipe of plan 3a (the branch's frontend built beside the stack in a verify container), at 1440 and 390 px, with a one-day admin key and a one-day operator key, screenshots in the PR:

1. `/merge-approvals`: the list filters; an ask's page shows its sections; the operator sees Approve disabled; the admin's Approve opens the confirm and, on a dry-run deployment, the toast reads the outcome.
2. `/pipelines`: the built-in badge, a Make default from the menu, the 409 toast when the kind is taken, Clear default, Duplicate through its dialog.
3. `/`: the deep stats under the panels; the Fleet rows that the deployment wires.
4. `/qa/repos/<repo>`: Build QA image with an unregistered branch name shows the server's refusal in the form.
5. `/repos/<repo>`: the Effective switch and the Source column.
6. `/watcher?tab=stacks`: the list, an edit with an unknown settings key dropped and named, a Disable.
7. `/hosts`: a probe, an edit refused by the server with the form kept, an import confirm (do not confirm on a deployment without a tailnet key: cancel it).
8. `/orgs/<owner>?tab=settings`: the webhook card, Pause and Resume, the Remove confirm cancelled.
