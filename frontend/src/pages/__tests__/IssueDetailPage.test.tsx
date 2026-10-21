import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockCancel = vi.fn();
const mockToastSuccess = vi.fn();

vi.mock("sonner", () => ({
  toast: { success: (m: string) => mockToastSuccess(m), error: vi.fn() },
}));

const NOW = new Date().toISOString();
const ISSUE = {
  id: "iss-1",
  title: "Fix the login redirect",
  body: "",
  status: "open",
  priority: "medium",
  labels: [],
  repo: "acme/backend",
  parent_issue_id: null,
  tracker: null,
  tracker_key: null,
  tracker_url: null,
  github_sync_enabled: false,
  github_synced_at: null,
  created_by: "user",
  created_at: NOW,
  updated_at: NOW,
  children: [],
  children_count: 0,
  children_closed_count: 0,
  jobs: [],
  jobs_count: 0,
  active_agent: null,
  notification_channels: ["dashboard"],
};

vi.mock("@/hooks/useIssues", () => ({
  useIssue: () => ({ data: ISSUE, isLoading: false, isError: false, refetch: vi.fn() }),
  useIssueActivity: () => ({ data: [] }),
  useTriageIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteIssue: () => ({
    mutate: (id: string, opts: { onSuccess?: () => void }) => {
      mockCancel(id);
      opts.onSuccess?.();
    },
    isPending: false,
  }),
  useUpdateIssue: () => ({ mutate: vi.fn() }),
}));
vi.mock("@/components/issues/IssueActivityFeed", () => ({ IssueActivityFeed: () => null }));
vi.mock("@/components/issues/IssueQuestionsPanel", () => ({ IssueQuestionsPanel: () => null }));

import { IssueDetailPage } from "../IssueDetailPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/issues/iss-1"]}>
        <Routes>
          <Route path="/issues/:id" element={<IssueDetailPage />} />
          <Route path="/issues" element={<p>issue list</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IssueDetailPage — Cancel (#450)", () => {
  it("asks to cancel the issue and its jobs, not to delete it permanently", async () => {
    const confirmSpy = vi.fn(() => true);
    vi.stubGlobal("confirm", confirmSpy);
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    const question = String((confirmSpy.mock.calls[0] as unknown[])[0]);
    expect(question, "the backend soft-cancels; the prompt must not promise a delete").not.toMatch(
      /delete|cannot be undone/i,
    );
    expect(question).toBe("Cancel this issue? Its running jobs are cancelled too.");
    expect(mockCancel).toHaveBeenCalledWith("iss-1");
    expect(mockToastSuccess).toHaveBeenCalledWith("Issue cancelled");
  });

  it("stays on the issue, which still exists, after cancelling it", async () => {
    vi.stubGlobal("confirm", () => true);
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByText("issue list"), "the cancelled issue stays; do not leave it as if deleted").toBeNull();
    expect(screen.getByText("Fix the login redirect")).toBeInTheDocument();
  });

  it("does nothing when the cancel is not confirmed", async () => {
    vi.stubGlobal("confirm", () => false);
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(mockCancel).not.toHaveBeenCalled();
  });
});

describe("IssueDetailPage — sentence case", () => {
  afterEach(() => {
    Object.assign(ISSUE, { children: [], children_count: 0 });
  });

  it("names the statuses in the status picker in sentence case", () => {
    renderPage();
    const picker = screen.getByDisplayValue("Open") as HTMLSelectElement;
    const labels = [...picker.options].map((o) => o.textContent);
    expect(labels).toEqual(["Open", "Triaging", "Awaiting input", "In progress", "Closed", "Cancelled"]);
  });

  it("titles the child issues card in sentence case", () => {
    Object.assign(ISSUE, {
      children: [{ ...ISSUE, id: "iss-2", title: "Child fix", children: [] }],
      children_count: 1,
    });
    renderPage();
    expect(screen.getByText("Child issues")).toBeInTheDocument();
  });
});
