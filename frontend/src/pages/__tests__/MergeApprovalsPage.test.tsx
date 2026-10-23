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
    // The row's own "Dry run" pill matches too, so the mode line is read by its id.
    expect(screen.getByTestId("merge-approvals-mode")).toHaveTextContent(/dry run/i);
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
