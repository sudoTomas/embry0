import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockUseIssues = vi.fn();
const mockFetchProposals = vi.fn();

vi.mock("@/hooks/useIssues", () => ({
  useIssues: () => mockUseIssues(),
  useIssueBoard: () => ({ issues: [], totals: {}, isLoading: false, isError: false, refetch: vi.fn() }),
  useUpdateIssue: () => ({ mutate: vi.fn() }),
}));
vi.mock("@/api/cases", () => ({
  fetchProposals: () => mockFetchProposals(),
  acceptCase: vi.fn(),
  rejectCase: vi.fn(),
}));

import { IssuesPage } from "../IssuesPage";

const NOW = new Date().toISOString();

const ISSUE = {
  id: "iss-1",
  title: "An accepted piece of work",
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
  children_count: 0,
  children_closed_count: 0,
  jobs_count: 0,
  active_agent: null,
  notification_channels: [],
};

const PROPOSAL = {
  id: "hc-1",
  repo: "acme/backend",
  title: "A draft nobody answered",
  origin: "watcher",
  stage: "tracked",
  severity: null,
  tracker: { kind: "linear", key: "ENG-1", url: "https://linear.app/acme/issue/ENG-1" },
  timestamps: { detected_at: NOW, tracked_at: NOW },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <IssuesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseIssues.mockReturnValue({
    data: { issues: [ISSUE], total: 1, offset: 0, limit: 20 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

describe("IssuesPage", () => {
  it("shows the watcher's unanswered drafts above the issue list, not as issues", async () => {
    mockFetchProposals.mockResolvedValue({ cases: [PROPOSAL], total: 1, limit: 50, next_cursor: null });
    renderPage();

    const draft = await screen.findByText("A draft nobody answered");
    const issue = screen.getByText("An accepted piece of work");
    expect(draft.compareDocumentPosition(issue) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // A draft is a case, so it is not a row of the issue table.
    expect(draft.closest("table")).not.toBe(issue.closest("table"));
  });

  it("is the plain issue list when no draft is waiting", async () => {
    mockFetchProposals.mockResolvedValue({ cases: [], total: 0, limit: 50, next_cursor: null });
    renderPage();
    expect(await screen.findByText("An accepted piece of work")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Proposals" })).not.toBeInTheDocument();
  });
});
