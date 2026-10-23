import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { useAuthStore } from "@/stores/authStore";

const mockUseMergeApproval = vi.fn();
const mutate = vi.fn();
vi.mock("@/hooks/useMergeApprovals", () => ({
  useMergeApproval: (id: string | null) => mockUseMergeApproval(id),
  useDecideMergeApproval: () => ({ mutate, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), warning: vi.fn(), error: vi.fn() } }));
const { mockToastApiError } = vi.hoisted(() => ({ mockToastApiError: vi.fn() }));
vi.mock("@/api/errorToast", () => ({ toastApiError: mockToastApiError }));

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

function tree(path: string) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/merge-approvals/:approvalId" element={<MergeApprovalPage />} />
      </Routes>
    </MemoryRouter>
  );
}

/** Renders the page; `refetched()` re-renders it, the way the invalidated query hands the page its new row. */
function renderPage(path = "/merge-approvals/ma-1") {
  const view = render(tree(path));
  return { ...view, refetched: () => view.rerender(tree(path)) };
}

/** The decision's answer, as the service's `_result` shapes it. */
function answer(outcome: string, extra: Record<string, unknown> = {}) {
  return { outcome, status: ROW.status, dry_run: ROW.dry_run, pr_url: ROW.pr_url, head_sha: ROW.head_sha, ...extra };
}

type MutateOptions = { onSuccess: (r: unknown) => void; onError: (e: unknown) => void };

beforeEach(() => {
  mutate.mockReset();
  mockToastApiError.mockReset();
  mockUseMergeApproval.mockReset().mockReturnValue(ready(ROW));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("MergeApprovalPage", () => {
  it("names the ask and shows the change, the recommendation, the checks, the review and the denylisted paths", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "acme/widgets#42" })).toBeInTheDocument();
    expect(screen.getByText("Fix the widget")).toBeInTheDocument();
    expect(screen.getByText("embry0/fix → main")).toBeInTheDocument();
    // The verdict pill sits in the header and again at the top of the Recommendation section.
    expect(screen.getAllByText("Review first")).toHaveLength(2);
    expect(screen.getByText("a denylisted path")).toBeInTheDocument();
    expect(screen.getByText("all 2 checks passed")).toBeInTheDocument();
    expect(screen.getByText("Verification gate: Pass.")).toBeInTheDocument();
    expect(screen.getByText(/addresses the failure: Yes/)).toBeInTheDocument();
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

  it("labels the approval plainly in dry run, where nothing merges", async () => {
    mockUseMergeApproval.mockReturnValue(ready({ ...ROW, dry_run: true }));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Approve acme/widgets#42?" });
    expect(dialog).toHaveTextContent(/nothing merges/i);
    expect(within(dialog).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Approve and merge" })).toBeNull();
  });

  it("shows the outcome when the ask was decided elsewhere first", async () => {
    const decided = { ...ROW, status: "approved", decided_by: "ops@example.test", decided_at: "2026-10-20T11:00:00Z", chat_id: "chat-1" };
    mutate.mockImplementation((_vars: unknown, opts: MutateOptions) => {
      // The hook invalidates every merge-approval read on settle; the page then sees the decided row.
      mockUseMergeApproval.mockReturnValue(ready(decided));
      opts.onSuccess(answer("already_decided", { status: "approved", message: "this ask is already approved; nothing was done" }));
    });
    const { refetched } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and merge" }));
    expect(toast.info).toHaveBeenCalledWith("acme/widgets#42: Already decided");
    refetched();
    expect(screen.getByText(/decided by ops@example.test in the approval chat/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("warns with the reason when the merge was refused, and keeps the reason on the pending ask", async () => {
    const refused = { ...ROW, detail: "the head moved since the ask" };
    mutate.mockImplementation((_vars: unknown, opts: MutateOptions) => {
      mockUseMergeApproval.mockReturnValue(ready(refused));
      opts.onSuccess(answer("refused", { detail: refused.detail }));
    });
    const { refetched } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and merge" }));
    expect(toast.warning).toHaveBeenCalledWith("acme/widgets#42: Refused. the head moved since the ask");
    expect(toast.success).not.toHaveBeenCalled();
    refetched();
    expect(screen.getByTestId("merge-approval-detail")).toHaveTextContent("The head moved since the ask.");
    expect(screen.getByRole("button", { name: "Approve" }), "the ask is still pending, so the decision stays offered").toBeEnabled();
  });

  it("celebrates a merge", async () => {
    mutate.mockImplementation((_vars: unknown, opts: MutateOptions) => opts.onSuccess(answer("merged", { status: "merged" })));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve and merge" }));
    expect(toast.success).toHaveBeenCalledWith("acme/widgets#42: Merged");
  });

  it("explains a refused request through the api error toast", async () => {
    const error = { response: { status: 403, data: { detail: "Admin scope required" } } };
    mutate.mockImplementation((_vars: unknown, opts: MutateOptions) => opts.onError(error));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject the ask" }));
    expect(mockToastApiError).toHaveBeenCalledWith(error, "The decision was not recorded");
    expect(screen.queryByRole("dialog"), "the confirm closes either way").toBeNull();
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
